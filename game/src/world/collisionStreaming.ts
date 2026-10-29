// Streams .col tiles within `radius` metres of the focus point into the physics worker, and the tiles along the next
// AHEAD_SECONDS of travel when the player moves; unloads the rest beyond radius + hysteresis. It also tells the worker
// which neighbours of the loaded tiles have collision somewhere (the manifest, drafts included, minus tiles that failed
// for good): edge walls stand only where none does (physics/edgeWalls.ts).
import { collisionCovers } from '../physics/collisionCoverage';
import { loading, loadBytes } from '../core/loading';
import { COLLISION_TILE, collisionTileId, distToCollisionTile, parseCollisionId } from '../core/origin';
import type { Manifest, ManifestEntry } from './manifest';
import type { PhysicsClient } from '../physics/client';

/** Seconds of travel along the heading whose collision is loaded ahead, beyond the radius (collision tiles are small). */
const AHEAD_SECONDS = 8;
/** Covers the complete 80 m safe-street search. Runtime swept coverage still guards every actor. */
export const STARTUP_COLLISION_RADIUS = 80;
/** Below this speed (m/s) nothing is loaded ahead. */
const AHEAD_MIN_SPEED = 3;
const SIDES = [[1, 0], [-1, 0], [0, 1], [0, -1]] as const;

type Physics = Pick<PhysicsClient, 'loaded' | 'loadTile' | 'unloadTile' | 'setKnown' | 'setTileLoading' | 'onError'>;

export class CollisionStreamer {
  private loading = new Set<string>();
  private failed = new Set<string>();
  private retries = new Map<string, { attempts: number; at: number }>();
  /** Tiles the worker was told exist (edgeWalls.ts `known`). */
  private known = new Set<string>();
  private lastCheck = -Infinity;
  private prepared = new Map<string, { entry: ManifestEntry; buffer: ArrayBuffer }>();
  retain(id: string, entry: ManifestEntry, buffer: ArrayBuffer) { this.prepared.set(id, { entry, buffer }); }
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
  retainedStarts: readonly { x: number; z: number }[] = [];

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
    // Residency bounds the cache; consumed buffers transfer directly to the worker.
    for (const [id, cached] of this.prepared) {
      if (this.manifest.collision[id]?.url !== cached.entry.url) this.prepared.delete(id);
    }
    const ahead = this.ahead(x, z, vx, vz);
    const retained = this.retainedStarts.flatMap(p => this.near(p.x, p.z, this.radius));
    const keep = new Set([...ahead, ...retained].map(t => t.id));
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
      const byId = new Map([...this.near(x, z, this.radius), ...ahead, ...retained].map(t => [t.id, t]));
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
    this.physics.setTileLoading(id, true);
    const done = loading.begin(`Collision ${id} .col · ${loadBytes(e.bytes)}`);
    try {
      const cached = this.prepared.get(id);
      this.prepared.delete(id);
      let buf: ArrayBuffer;
      if (cached?.entry.url === e.url && cached.entry.sha256 === e.sha256) buf = cached.buffer;
      else {
        const res = await fetch(new URL(e.url, this.manifest.baseUrl), { signal: AbortSignal.timeout(15_000) });
        // A failed/in-progress draft can temporarily answer 404 during repair; retry its payload.
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        buf = await res.arrayBuffer();
      }
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
      this.physics.setTileLoading(id, false);
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
  /** Actual worker-acknowledged coverage, also for stationary actors outside the manifest. */
  supports(x: number, z: number, radius = 0.32): boolean {
    return collisionCovers(this.physics.loaded, x, z, x, z, radius);
  }
  get pending(): number { return this.loading.size; }
  /** Ids within radius that are not loaded yet (ignores failed ones). */
  missingNear(x: number, z: number, r: number): string[] {
    return this.near(x, z, r).map((t) => t.id).filter((id) => !this.physics.loaded.has(id) && !this.failed.has(id));
  }

  /** Counts worker-decoded cells, not completed HTTP requests. Failed required cells prevent startup. */
  progressAt(x: number, z: number) {
    const wanted = [...new Set([{ x, z }, ...this.retainedStarts].flatMap(p => this.near(p.x, p.z, this.radius).map(t => t.id)))];
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
}
