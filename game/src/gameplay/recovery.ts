// Safe reset: the playable area (the union of the published collision tiles), the last safe positions and the
// "lost" rules for a player who fell off the built area or into the river. Pure logic; main.ts feeds it a probe
// every frame, ui/recovery.ts shows the prompt and main.ts performs the reset. See README.md "Safe reset".
import { COLLISION_TILE, collisionTileId, distToCollisionTile, parseCollisionId } from '../core/origin';
import type { WorldPoint } from './contract';

export const RECOVERY = {
  /** Seconds between safe samples, and how many seconds of samples the ring buffer keeps. */
  sampleEvery: 1, keepSeconds: 10,
  /** The reset uses the newest sample at least this long before the player stopped being safe. */
  lookBack: 3,
  /** Lost below the lowest loaded ground minus this, or this far outside the area's bounding box (m). */
  belowGround: 5, outsideArea: 5,
  /** Lost after this long continuously under the river surface, or without any ground contact (s). */
  waterSeconds: 2, airSeconds: 3,
  /** Feet (or the car body) this far below the water surface count as in the river (m). */
  waterDepth: 0.3,
  /** Safe samples: at most this vertical speed (m/s), i.e. no fall or slide. */
  maxVerticalSpeed: 2,
  /** Automatic reset after this countdown (s); the reset line fades over `fade` seconds. */
  countdown: 5, fade: 3,
  /** The reset lands this far inside the area's edge (m); a car adds half its length. */
  edgeMargin: 2, carMargin: 2.3,
  /** The HUD and the minimap mark the edge of the built area within this distance (m). */
  edgeWarning: 20,
  /** Assessment pauses this long after a reset, until the physics state reflects the new position (s). */
  grace: 0.5,
} as const;

/** The playable area: the union of collision tiles in the manifest (since 57eaed4 a tile list, not a rectangle). */
export class PlayableArea {
  readonly bbox: { x0: number; z0: number; x1: number; z1: number };
  private readonly cells = new Set<string>();

  constructor(ids: Iterable<string>) {
    let x0 = Infinity, z0 = Infinity, x1 = -Infinity, z1 = -Infinity;
    for (const id of ids) {
      const ij = parseCollisionId(id);
      if (!ij) continue;
      this.cells.add(id);
      x0 = Math.min(x0, ij.i * COLLISION_TILE); x1 = Math.max(x1, (ij.i + 1) * COLLISION_TILE);
      z0 = Math.min(z0, ij.j * COLLISION_TILE); z1 = Math.max(z1, (ij.j + 1) * COLLISION_TILE);
    }
    this.bbox = { x0, z0, x1, z1 };
  }

  hasCell(i: number, j: number) { return this.cells.has(collisionTileId(i, j)); }
  has(x: number, z: number) { return this.hasCell(Math.floor(x / COLLISION_TILE), Math.floor(z / COLLISION_TILE)); }

  /** Distance outside the bounding box of the whole area (0 inside it). */
  outsideBy(x: number, z: number) {
    const { x0, z0, x1, z1 } = this.bbox;
    return Math.hypot(Math.max(x0 - x, 0, x - x1), Math.max(z0 - z, 0, z - z1));
  }

  /** Distance from a point inside the area to its edge (holes included), or Infinity beyond `limit`; 0 outside. */
  edgeDistance(x: number, z: number, limit: number = RECOVERY.edgeWarning) {
    if (!this.has(x, z)) return 0;
    let best = Infinity;
    const r = Math.ceil(limit / COLLISION_TILE);
    const ci = Math.floor(x / COLLISION_TILE), cj = Math.floor(z / COLLISION_TILE);
    for (let i = ci - r; i <= ci + r; i++) for (let j = cj - r; j <= cj + r; j++) {
      if (!this.hasCell(i, j)) best = Math.min(best, distToCollisionTile(x, z, i, j));
    }
    return best <= limit ? best : Infinity;
  }

  /** Moves a point of the area at least `margin` away from every cell outside it. */
  inset(p: WorldPoint, margin: number): WorldPoint {
    let { x, z } = p;
    for (let pass = 0; pass < 4; pass++) {
      let moved = false;
      const ci = Math.floor(x / COLLISION_TILE), cj = Math.floor(z / COLLISION_TILE);
      for (let i = ci - 1; i <= ci + 1; i++) for (let j = cj - 1; j <= cj + 1; j++) {
        if (this.hasCell(i, j)) continue;
        const cx = Math.min(Math.max(x, i * COLLISION_TILE), (i + 1) * COLLISION_TILE);
        const cz = Math.min(Math.max(z, j * COLLISION_TILE), (j + 1) * COLLISION_TILE);
        const d = Math.hypot(x - cx, z - cz);
        if (d >= margin || d === 0) continue;
        x = cx + (x - cx) / d * margin; z = cz + (z - cz) / d * margin;
        moved = true;
      }
      if (!moved) break;
    }
    return { x, z };
  }

  /** Every cell (i, j) of the area within the square [x0, x1] x [z0, z1]. */
  *cellsIn(x0: number, z0: number, x1: number, z1: number) {
    for (let i = Math.floor(x0 / COLLISION_TILE); i <= Math.floor(x1 / COLLISION_TILE); i++) {
      for (let j = Math.floor(z0 / COLLISION_TILE); j <= Math.floor(z1 / COLLISION_TILE); j++) {
        if (this.hasCell(i, j)) yield [i, j] as const;
      }
    }
  }
}

/** The lowest ground (manifest `h[0]`) of the given collision tiles. */
export function lowestGround(ids: Iterable<string>, collision: Record<string, { h: [number, number] }>) {
  let low = Infinity;
  for (const id of ids) low = Math.min(low, collision[id]?.h[0] ?? Infinity);
  return low;
}

export interface SafeSample { time: number; x: number; y: number; z: number; heading: number; inVehicle: boolean }

/** What the frame loop knows about the player (on foot: the feet; in a car: its body). */
export interface Probe extends SafeSample {
  /** On foot: the character is grounded. In a car: a wheel touches the ground. */
  grounded: boolean;
  /** The collision tile under the player is loaded. */
  loaded: boolean;
  /** Vertical speed (m/s). */
  vy: number;
  /** In a car: at least three wheels touch and the body is upright. Always true on foot. */
  steady: boolean;
  /** How far below the river surface the player is (m, negative above it), or null without water below. */
  waterDepth: number | null;
}

export type LostReason = 'below' | 'outside' | 'water' | 'airborne';

/** Tracks the safe samples and decides when the player is lost and when the countdown resets them. */
export class RecoveryMonitor {
  readonly samples: SafeSample[] = [];
  lost: { reason: LostReason; since: number } | null = null;
  resets = 0;
  private lastSample = -Infinity;
  private lastSafe = -Infinity;
  private waterSince: number | null = null;
  private airSince: number | null = null;
  private graceUntil = -Infinity;

  constructor(private readonly area: PlayableArea, private readonly fallback: SafeSample) {}

  /** Seconds left before the automatic reset, or null when not lost. */
  countdown(time: number) { return this.lost ? Math.max(0, this.lost.since + RECOVERY.countdown - time) : null; }

  safe(p: Probe) {
    return p.grounded && p.steady && p.loaded && this.area.has(p.x, p.z) && (p.waterDepth ?? -1) <= 0
      && Math.abs(p.vy) <= RECOVERY.maxVerticalSpeed;
  }

  /** Call every unpaused frame. `minGround` is the lowest ground of the loaded tiles. True: reset now. */
  update(p: Probe, minGround: number): boolean {
    if (p.time < this.graceUntil) return false;
    const safe = this.safe(p);
    if (safe) {
      this.lastSafe = p.time;
      if (p.time - this.lastSample >= RECOVERY.sampleEvery) this.record(p);
    }
    this.waterSince = (p.waterDepth ?? -1) > RECOVERY.waterDepth ? this.waterSince ?? p.time : null;
    this.airSince = p.grounded ? null : this.airSince ?? p.time;
    const reason = lostReason(p, minGround, this.area, this.waterSince, this.airSince);
    if (reason && !this.lost) this.lost = { reason, since: p.time };
    // A fall that lands on solid ground inside the area (a long jump, a high kerb) needs no reset.
    else if (!reason && this.lost && safe) this.lost = null;
    return this.lost !== null && p.time >= this.lost.since + RECOVERY.countdown;
  }

  /** The newest sample from `lookBack` seconds before the player stopped being safe, moved inside the edge. */
  target(): SafeSample {
    const before = this.lastSafe - RECOVERY.lookBack;
    const sample = [...this.samples].reverse().find(s => s.time <= before) ?? this.samples[0] ?? this.fallback;
    const margin = RECOVERY.edgeMargin + (sample.inVehicle ? RECOVERY.carMargin : 0);
    return { ...sample, ...this.area.inset(sample, margin) };
  }

  /** The player was put back at `target` at `time`: newer samples led to the fall and are dropped. */
  recovered(target: SafeSample, time: number) {
    const keep = this.samples.findIndex(s => s.time > target.time);
    if (keep >= 0) this.samples.length = keep;
    this.lost = null;
    this.waterSince = this.airSince = null;
    this.lastSafe = time;
    this.lastSample = time - RECOVERY.sampleEvery;
    this.graceUntil = time + RECOVERY.grace;
    this.resets++;
  }

  private record(p: Probe) {
    this.lastSample = p.time;
    this.samples.push({ time: p.time, x: p.x, y: p.y, z: p.z, heading: p.heading, inVehicle: p.inVehicle });
    while (this.samples.length && this.samples[0].time < p.time - RECOVERY.keepSeconds) this.samples.shift();
  }
}

/** The lost rules, the same for the character and the car. */
export function lostReason(p: Probe, minGround: number, area: PlayableArea, waterSince: number | null,
  airSince: number | null): LostReason | null {
  if (Number.isFinite(minGround) && p.y < minGround - RECOVERY.belowGround) return 'below';
  if (area.outsideBy(p.x, p.z) > RECOVERY.outsideArea) return 'outside';
  if (waterSince !== null && p.time - waterSince > RECOVERY.waterSeconds) return 'water';
  if (airSince !== null && p.time - airSince > RECOVERY.airSeconds) return 'airborne';
  return null;
}
