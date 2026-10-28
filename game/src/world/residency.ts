// L0 residency: the playable area is a growing tile list, so at most RESIDENT_L0 full-detail tiles may be
// refined at once. Refinement is decided per 500 m parent (REPLACE swaps a parent for all its L0 children):
// parents are taken nearest first while their children still fit the cap; the rest stay coarse.
import { VISUAL_L0 } from '../core/origin';

export const RESIDENT_L0 = 9;
/** One L0 cell of look-ahead in every direction; retained parents get another half cell before eviction. */
export const VISUAL_RING_RADIUS = VISUAL_L0;
const RING_HYSTERESIS = VISUAL_L0 / 2;

/** REPLACE parents intersecting the player's ring. Every sibling must stay resident to replace the parent;
 * a count cap must never exclude a tile inside the ring. Rank by nearest child, independent of camera heading. */
export function ringParents(parents: ReadonlyMap<string, readonly string[]>, x: number, z: number,
  previous: ReadonlySet<string> = new Set(), radius = VISUAL_RING_RADIUS): Set<string> {
  return new Set([...parents].map(([id, children]) => ({ id,
    d: Math.min(...children.map(child => distToVisualTile(child, x, z))) }))
    .filter(({ id, d }) => d <= radius + (previous.has(id) ? RING_HYSTERESIS : 0))
    .sort((a, b) => a.d - b.d || a.id.localeCompare(b.id)).map(({ id }) => id));
}

/** Distance (m) from (x,z) to the footprint of visual tile id `t{L}_{i}_{j}`; Infinity for other ids. */
export function distToVisualTile(id: string, x: number, z: number): number {
  const m = /^t(\d+)_(-?\d+)_(-?\d+)$/.exec(id);
  if (!m) return Infinity;
  const size = VISUAL_L0 * 2 ** +m[1], x0 = +m[2] * size, z0 = +m[3] * size;
  return Math.hypot(Math.max(x0 - x, 0, x - (x0 + size)), Math.max(z0 - z, 0, z - (z0 + size)));
}

/** Parents (id -> its L0 child ids) that may refine at (x,z): nearest first, total children <= cap.
 *  The nearest parent always refines so the player never stands on a coarse tile. */
export function refinableParents(parents: ReadonlyMap<string, readonly string[]>, x: number, z: number,
  cap = RESIDENT_L0): Set<string> {
  const ranked = [...parents.keys()].map((id) => ({ id, d: distToVisualTile(id, x, z) }))
    .sort((a, b) => a.d - b.d || (a.id < b.id ? -1 : 1));
  const out = new Set<string>();
  let resident = 0;
  for (const { id } of ranked) {
    const n = parents.get(id)!.length;
    if (out.size > 0 && resident + n > cap) continue;
    out.add(id); resident += n;
  }
  return out;
}
