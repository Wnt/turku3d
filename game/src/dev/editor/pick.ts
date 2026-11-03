// Centre-ray picking against editable placements as vertical cylinders (base, height, radius): no triangle raycasts,
// so instanced trees, merged prop batches and client-drawn posts pick the same way, by their source placement.
import type { V3 } from './catalog';

export interface Pickable { key: string; base: V3; height: number; radius: number }
export interface Candidate<T extends Pickable> { item: T; /** Ray distance (m) to the closest approach. */ t: number;
  /** Angle (rad) by which the ray misses the cylinder; 0 when it passes through. */ miss: number }

export const PICK = {
  /** Farthest pick (m): street furniture switches off at 125 m (world/propLod.ts). */
  far: 120,
  /** Candidates within this angle of the centre ray (rad, about 2.3°). */
  slack: 0.04,
  /** An occluding surface hides candidates more than this far behind it (m). */
  behind: 0.75,
} as const;

/** Closest approach of the ray (origin o, unit direction d) to the vertical segment base..base+height: the ray
 *  parameter t and the distance between the two closest points. */
export function rayToVertical(o: V3, d: V3, base: V3, height: number): { t: number; distance: number } {
  // Segment direction is +Y. Solve for ray t and segment s minimising |o + t d - (base + s up)|.
  const wx = o.x - base.x, wy = o.y - base.y, wz = o.z - base.z;
  const b = d.y, dd = 1, e = d.x * wx + d.y * wy + d.z * wz, f = wy;
  const denom = dd - b * b;
  let t: number, s: number;
  if (denom < 1e-9) { t = 0; s = f; }
  else { t = (b * f - e) / denom; s = (dd * f - b * e) / denom; }
  s = Math.min(height, Math.max(0, s));
  // Re-solve t for the clamped s, and keep it in front of the camera.
  t = Math.max(0, -(d.x * wx + d.y * (wy - s) + d.z * wz));
  const px = o.x + d.x * t - base.x, py = o.y + d.y * t - base.y - s, pz = o.z + d.z * t - base.z;
  return { t, distance: Math.hypot(px, py, pz) };
}

/** Editables the centre ray points at, best first: the smallest angular miss, then the nearest. `occluder` is the ray
 *  distance of the first solid surface that is not an editable itself (null: none). */
export function pickCandidates<T extends Pickable>(o: V3, d: V3, items: Iterable<T>, occluder: number | null = null,
  far: number = PICK.far, slack: number = PICK.slack): Candidate<T>[] {
  const out: Candidate<T>[] = [];
  for (const item of items) {
    const { t, distance } = rayToVertical(o, d, item.base, item.height);
    if (t <= 0.05 || t > far) continue;
    if (occluder !== null && t - item.radius > occluder + PICK.behind) continue;
    const miss = Math.max(0, distance - item.radius) / t;
    if (miss > slack) continue;
    out.push({ item, t, miss });
  }
  // Direct hits (miss 0) nearest first; near misses after them by angle.
  return out.sort((a, b) => (a.miss - b.miss) || (a.t - b.t));
}

/** Keeps the cycled choice on the same placement while the candidate list changes under a moving camera. */
export class CandidateCycle<T extends Pickable> {
  list: Candidate<T>[] = [];
  private chosen: string | null = null;
  private offset = 0;

  update(list: Candidate<T>[]) {
    this.list = list;
    if (this.chosen === null) return;
    const i = list.findIndex(c => c.item.key === this.chosen);
    if (i < 0) { this.chosen = null; this.offset = 0; } else this.offset = i;
  }
  /** Step through the candidates (L1 -1, R1 +1), wrapping. */
  cycle(step: number) {
    if (!this.list.length) return;
    this.offset = ((this.offset + step) % this.list.length + this.list.length) % this.list.length;
    this.chosen = this.list[this.offset].item.key;
  }
  reset() { this.chosen = null; this.offset = 0; }
  get index() { return this.list.length ? Math.min(this.offset, this.list.length - 1) : -1; }
  get current(): Candidate<T> | null { return this.list[this.index] ?? null; }
}
