// Streams .col tiles within `radius` metres of the focus point into the physics worker, and the tiles along the next
// AHEAD_SECONDS of travel when the player moves; unloads the rest beyond radius + hysteresis. It also tells the worker
// which neighbours of the loaded tiles have collision somewhere (the manifest, drafts included, minus tiles that failed
// for good): edge walls stand only where none does (physics/edgeWalls.ts). Near a tile that exists but is not loaded
// yet, `speedLimit` holds the player gently instead.
import { loading, loadBytes } from '../core/loading';
import { COLLISION_TILE, collisionTileId, distToCollisionTile, parseCollisionId } from '../core/origin';
import type { Manifest } from './manifest';
import type { PhysicsClient } from '../physics/client';
import type { CharacterInput, VehicleInput } from '../physics/protocol';

/** Seconds of travel along the heading whose collision is loaded ahead, beyond the radius (collision tiles are small). */
const AHEAD_SECONDS = 8;
/** Below this speed (m/s) nothing is loaded ahead. */
const AHEAD_MIN_SPEED = 3;
/** The hold near unloaded collision: the speed that stops within the distance left at this deceleration (m/s²),
 *  minus a margin (m), checked within `HOLD_RANGE` (m). */
const HOLD = { decel: 6, margin: 1.5, range: 80 } as const;
const SIDES = [[1, 0], [-1, 0], [0, 1], [0, -1]] as const;

type Physics = Pick<PhysicsClient, 'loaded' | 'loadTile' | 'unloadTile' | 'setKnown' | 'onError'>;

export class CollisionStreamer {
  private loading = new Set<string>();
  private failed = new Set<string>();
  private retries = new Map<string, { attempts: number; at: number }>();
  /** Tiles the worker was told exist (edgeWalls.ts `known`). */
  private known = new Set<string>();
  private lastCheck = -Infinity;
  readonly maxConcurrent = 4;
  /** Tile ids within `r` of (x,z), found through the i/j grid window instead of a scan over the whole manifest
   *  (a city-wide manifest has ~80k collision tiles: a full scan with id parsing costs ~20 ms per call). */
  private near(x: number, z: number, r: number): { id: string; d: number }[] {
    const out: { id: string; d: number }[] = [];
    const i0 = Math.floor((x - r) / COLLISION_TILE), i1 = Math.floor((x + r) / COLLISION_TILE);
    const j0 = Math.floor((z - r) / COLLISION_TILE), j1 = Math.floor((z + r) / COLLISION_TILE);
    for (let i = i0; i <= i1; i++) for (let j = j0; j <= j1; j++) {
      const id = collisionTileId(i, j);
      if (!this.manifest.collision[id]) continue;
      const d = distToCollisionTile(x, z, i, j);
      if (d <= r) out.push({ id, d });
    }
    return out;
  }
  constructor(private manifest: Manifest, private physics: Physics, public radius = 200, public hysteresis = 50) {}

  /** Tiles along the next AHEAD_SECONDS of travel at (vx, vz) m/s beyond the radius, with a one-tile corridor, each
   *  with its distance from (x, z). Empty below AHEAD_MIN_SPEED. */
  ahead(x: number, z: number, vx: number, vz: number): { id: string; d: number }[] {
    const speed = Math.hypot(vx, vz);
    if (speed < AHEAD_MIN_SPEED) return [];
    const reach = this.radius + speed * AHEAD_SECONDS, steps = Math.ceil(reach / (COLLISION_TILE / 2));
    const out = new Map<string, number>();
    for (let k = 0; k <= steps; k++) {
      const px = x + vx / speed * reach * k / steps, pz = z + vz / speed * reach * k / steps;
      const ci = Math.floor(px / COLLISION_TILE), cj = Math.floor(pz / COLLISION_TILE);
      for (let i = ci - 1; i <= ci + 1; i++) for (let j = cj - 1; j <= cj + 1; j++) {
        const id = collisionTileId(i, j);
        if (this.manifest.collision[id] && !out.has(id)) out.set(id, distToCollisionTile(x, z, i, j));
      }
    }
    return [...out].map(([id, d]) => ({ id, d }));
  }

  /** Call every frame with the focus point and the player's ground velocity (m/s); checks at most 4x per second unless
   *  forced. Returns ids requested this call. */
  update(x: number, z: number, force = false, vx = 0, vz = 0): string[] {
    const now = performance.now();
    if (!force && now - this.lastCheck < 250) return [];
    this.lastCheck = now;
    const ahead = this.ahead(x, z, vx, vz);
    const keep = new Set(ahead.map(t => t.id));
    for (const id of [...this.physics.loaded.keys()]) {
      const ij = parseCollisionId(id);
      if (ij && !keep.has(id) && distToCollisionTile(x, z, ij.i, ij.j) > this.radius + this.hysteresis) this.physics.unloadTile(id);
    }
    const started: string[] = [];
    // Keep unloading while requests are in flight, but only search/sort when a slot is available.
    if (this.loading.size < this.maxConcurrent) {
      // Nearest first; a tile ahead counts nearer by half its distance along the heading.
      const speed = Math.hypot(vx, vz);
      const rank = ({ id, d }: { id: string; d: number }) => {
        const ij = parseCollisionId(id)!;
        const along = speed > 0 ? (((ij.i + 0.5) * COLLISION_TILE - x) * vx + ((ij.j + 0.5) * COLLISION_TILE - z) * vz) / speed : 0;
        return d - 0.5 * Math.max(0, along);
      };
      const byId = new Map([...this.near(x, z, this.radius), ...ahead].map(t => [t.id, t]));
      const want = [...byId.values()].filter(({ id }) => !this.physics.loaded.has(id) && !this.loading.has(id) && !this.failed.has(id)
        && (this.retries.get(id)?.at ?? 0) <= now).map(t => ({ id: t.id, r: rank(t) }));
      want.sort((a, b) => a.r - b.r);
      for (const { id } of want) {
        if (this.loading.size >= this.maxConcurrent) break;
        this.loading.add(id); started.push(id);
        void this.fetchAndLoad(id);
      }
    }
    this.syncKnown();
    return started;
  }

  /** The worker learns which neighbours of the loaded and loading tiles exist: a wall faces only the others. */
  private syncKnown() {
    const next = new Set<string>();
    for (const id of [...this.physics.loaded.keys(), ...this.loading]) {
      const ij = parseCollisionId(id);
      if (!ij) continue;
      for (const [di, dj] of [[0, 0], ...SIDES]) {
        const n = collisionTileId(ij.i + di, ij.j + dj);
        if (this.manifest.collision[n] && !this.failed.has(n)) next.add(n);
      }
    }
    const add = [...next].filter(id => !this.known.has(id)), remove = [...this.known].filter(id => !next.has(id));
    this.known = next;
    this.physics.setKnown(add, remove);
  }

  private async fetchAndLoad(id: string) {
    const e = this.manifest.collision[id];
    const done = loading.begin(`Collision ${id} .col · ${loadBytes(e.bytes)}`);
    try {
      const res = await fetch(new URL(e.url, this.manifest.baseUrl), { signal: AbortSignal.timeout(15_000) });
      if (!res.ok) throw Object.assign(new Error(`HTTP ${res.status}`), {
        permanent: res.status < 500 && res.status !== 408 && res.status !== 429,
      });
      const buf = await res.arrayBuffer();
      if (buf.byteLength !== e.bytes) throw Object.assign(new Error(`size ${buf.byteLength} != manifest ${e.bytes}`), { permanent: true });
      await this.physics.loadTile(id, buf);
      this.retries.delete(id);
      done('Rapier collider ready');
    } catch (err) {
      done(`FAILED: ${String(err)}`);
      const error = err as Error & { permanent?: boolean };
      if (error.permanent) {
        this.failed.add(id);
        this.retries.delete(id);
        this.physics.onError?.(`collision ${id}: ${error.message}`);
      } else {
        const attempts = (this.retries.get(id)?.attempts ?? 0) + 1;
        const delay = Math.min(30_000, 500 * 2 ** Math.min(attempts - 1, 6));
        this.retries.set(id, { attempts, at: performance.now() + delay });
        console.warn(`[collision] ${id}: ${error.message}; retry in ${delay} ms`);
      }
    } finally {
      this.loading.delete(id);
    }
  }
  /** A new tile release changed these entries: loaded ones are fetched again and replace their colliders in one
   *  worker message (no step in between), the rest load from the new entry when the player comes near. */
  refresh(ids: Iterable<string>) {
    for (const id of ids) {
      this.failed.delete(id);
      this.retries.delete(id);
      if (!this.physics.loaded.has(id) || this.loading.has(id) || !this.manifest.collision[id]) continue;
      this.loading.add(id);
      void this.fetchAndLoad(id);
    }
  }
  get pending(): number { return this.loading.size; }
  /** Ids within radius that are not loaded yet (ignores failed ones). */
  missingNear(x: number, z: number, r: number): string[] {
    return this.near(x, z, r).map((t) => t.id).filter((id) => !this.physics.loaded.has(id) && !this.failed.has(id));
  }

  /** Counts worker-decoded cells, not completed HTTP requests. Failed required cells prevent startup. */
  progressAt(x: number, z: number) {
    const wanted = this.near(x, z, this.radius).map(t => t.id);
    return { loaded: wanted.filter(id => this.physics.loaded.has(id)).length, total: wanted.length,
      errors: wanted.filter(id => this.failed.has(id)).map(id => `collision ${id} failed`) };
  }

  /** The true edges of the loaded collision as [i, j, side] (side: +x, -x, +z, -z): sides of a loaded tile whose
   *  neighbour has no collision in any source. The worker closes exactly these with edge walls. */
  trueEdges(): [number, number, number][] {
    const out: [number, number, number][] = [];
    for (const id of this.physics.loaded.keys()) {
      const ij = parseCollisionId(id);
      if (!ij) continue;
      SIDES.forEach(([di, dj], side) => {
        const n = collisionTileId(ij.i + di, ij.j + dj);
        if (!this.physics.loaded.has(n) && (!this.manifest.collision[n] || this.failed.has(n))) out.push([ij.i, ij.j, side]);
      });
    }
    return out;
  }

  /** The highest speed (m/s) at which a player at (x, z) moving at (vx, vz) still stops before a tile that exists but is
   *  not loaded yet and lies ahead (no edge wall faces it); Infinity when there is none within reach. */
  speedLimit(x: number, z: number, vx: number, vz: number): number {
    const speed = Math.hypot(vx, vz);
    if (speed < 0.1) return Infinity;
    let limit = Infinity;
    for (const { id, d } of this.near(x, z, HOLD.range)) {
      if (this.physics.loaded.has(id) || this.failed.has(id)) continue;
      const ij = parseCollisionId(id)!;
      // The nearest point of the tile lies ahead of the motion.
      const px = Math.min(Math.max(x, ij.i * COLLISION_TILE), (ij.i + 1) * COLLISION_TILE);
      const pz = Math.min(Math.max(z, ij.j * COLLISION_TILE), (ij.j + 1) * COLLISION_TILE);
      if (d > 0 && (px - x) * vx + (pz - z) * vz <= 0) continue;
      limit = Math.min(limit, Math.sqrt(2 * HOLD.decel * Math.max(0, d - HOLD.margin)));
    }
    return limit;
  }
}

/** A car faster than `limit` (CollisionStreamer.speedLimit) brakes gently instead of driving on. */
export function holdCar(input: VehicleInput, forwardSpeed: number, limit: number): VehicleInput {
  const excess = Math.abs(forwardSpeed) - limit;
  if (!(excess > 0)) return input;
  const pedal = Math.min(1, Math.max(0.3, excess / 3));
  // Forward motion brakes with the pedal; reversing brakes with the throttle (vehicle/handling.ts).
  return forwardSpeed >= 0 ? { ...input, throttle: 0, brake: pedal } : { ...input, throttle: pedal, brake: 0 };
}

/** The character's desired velocity, scaled down to `limit`. */
export function holdFoot(input: CharacterInput, limit: number): CharacterInput {
  const speed = Math.hypot(input.vx, input.vz);
  if (!(speed > limit)) return input;
  const k = limit / speed;
  return { ...input, vx: input.vx * k, vz: input.vz * k };
}
