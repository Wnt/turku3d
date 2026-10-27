// Level-0 visual tile ids (pipeline/turku3d/config.py: t0_<i>_<j>, i = floor(x / 250), j = floor(z / 250);
// negative indices outside the built area).
import { VISUAL_L0 } from '../core/origin';

export function visualTileAt(x: number, z: number): string {
  return `t0_${Math.floor(x / VISUAL_L0)}_${Math.floor(z / VISUAL_L0)}`;
}

/** The first `count` tiles after the one at (x, z) along the horizontal direction (dx, dz), in order. */
export function tilesAhead(x: number, z: number, dx: number, dz: number, count = 2): string[] {
  const len = Math.hypot(dx, dz);
  const out: string[] = [];
  if (len < 1e-9) return out;
  const here = visualTileAt(x, z);
  const step = VISUAL_L0 / 10;
  for (let d = step; out.length < count && d <= VISUAL_L0 * (count + 1); d += step) {
    const id = visualTileAt(x + dx / len * d, z + dz / len * d);
    if (id !== here && !out.includes(id)) out.push(id);
  }
  return out;
}
