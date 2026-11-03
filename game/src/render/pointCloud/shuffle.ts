import type { DecodedPoints } from './copc';

/** FNV-1a of a string: a stable seed per node key. */
function seed(key: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < key.length; i++) { h ^= key.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return h >>> 0 || 1;
}

/** Deterministic in-place Fisher-Yates over all point columns: any prefix is then a uniform subsample of the node,
 *  which distance thinning draws (lod.ts drawnPoints) without a second buffer. */
export function shufflePoints(points: Pick<DecodedPoints, 'count' | 'positions' | 'colors' | 'survey' | 'extra'>, key: string) {
  const { positions: p, colors: c, survey: v, extra: e } = points;
  let s = seed(key);
  for (let i = points.count - 1; i > 0; i--) {
    // xorshift32
    s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0;
    const j = s % (i + 1);
    for (let k = 0; k < 3; k++) { const t = p[i * 3 + k]; p[i * 3 + k] = p[j * 3 + k]; p[j * 3 + k] = t; }
    for (let k = 0; k < 4; k++) {
      const t = c[i * 4 + k]; c[i * 4 + k] = c[j * 4 + k]; c[j * 4 + k] = t;
      const u = e[i * 4 + k]; e[i * 4 + k] = e[j * 4 + k]; e[j * 4 + k] = u;
      const w = v[i * 4 + k]; v[i * 4 + k] = v[j * 4 + k]; v[j * 4 + k] = w;
    }
  }
}
