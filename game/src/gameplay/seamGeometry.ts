/** The generator moves a seam at most 3 m; two generations can differ by twice that, plus rounding. */
export const SEAM_MOVE_LIMIT = 6.1;
export interface SeamLine { points: Float64Array; cumulative: Float64Array; heights?: Float64Array | null }

export function arcLengths(p: Float64Array): Float64Array {
  const c = new Float64Array(p.length / 2);
  for (let i = 1; i < c.length; i++) c[i] = c[i - 1] + Math.hypot(p[2 * i] - p[2 * i - 2], p[2 * i + 1] - p[2 * i - 1]);
  return c;
}

function sample(line: SeamLine, s: number) {
  const { points: p, cumulative: c, heights: h } = line;
  let i = 1;
  while (i < c.length - 1 && c[i] < s) i++;
  const d = c[i] - c[i - 1], t = d > 0 ? (s - c[i - 1]) / d : 0;
  return { x: p[2 * i - 2] + t * (p[2 * i] - p[2 * i - 2]), z: p[2 * i - 1] + t * (p[2 * i + 1] - p[2 * i - 1]),
    tx: (p[2 * i] - p[2 * i - 2]) / (d || 1), tz: (p[2 * i + 1] - p[2 * i - 1]) / (d || 1),
    y: h ? h[i - 1] + t * (h[i] - h[i - 1]) : 0 };
}

/** Meet at one point and tangent. Cubic displacement dies with zero derivative within 20 m (at most 45% of
 * each edge, so the other junction/connector stays fixed). Heights follow the same blend. No snap or gap at the
 * seam, including an old neighbour whose original clip point is now up to 3 m from the centred lane. */
export function blendSeam(a: SeamLine, aStart: boolean, b: SeamLine, bStart: boolean): [SeamLine, SeamLine] | null {
  const al = a.cumulative.at(-1)!, bl = b.cumulative.at(-1)!;
  const ap = sample(a, aStart ? 0 : al), bp = sample(b, bStart ? 0 : bl);
  if (Math.hypot(ap.x - bp.x, ap.z - bp.z) > SEAM_MOVE_LIMIT) return null;
  const as = aStart ? 1 : -1, bs = bStart ? 1 : -1;
  let tx = ap.tx * as - bp.tx * bs, tz = ap.tz * as - bp.tz * bs;
  const norm = Math.hypot(tx, tz);
  if (norm < 1) return null; // not two continuing approaches
  tx /= norm; tz /= norm;
  if (Math.hypot(ap.x - bp.x, ap.z - bp.z) < 0.001 && Math.hypot(ap.tx * as - tx, ap.tz * as - tz) < 0.001
    && (!a.heights || !b.heights || Math.abs(ap.y - bp.y) < 0.001)) return null;
  const x = (ap.x + bp.x) / 2, z = (ap.z + bp.z) / 2, y = (ap.y + bp.y) / 2;
  const bend = (line: SeamLine, start: boolean, dx: number, dz: number): SeamLine => {
    const length = line.cumulative.at(-1)!, reach = Math.min(20, length * 0.45), sign = start ? 1 : -1;
    const end = sample(line, start ? 0 : length);
    const stations = new Set(Array.from(line.cumulative));
    for (let d = 0; d < reach; d += 0.5) stations.add(start ? d : length - d);
    stations.add(start ? reach : length - reach);
    const p: number[] = [], h: number[] = [];
    for (const s of [...stations].sort((u, v) => u - v)) {
      const base = sample(line, s), t = Math.min(1, (start ? s : length - s) / reach);
      const w = 2 * t ** 3 - 3 * t ** 2 + 1, v = (t ** 3 - 2 * t ** 2 + t) * reach;
      p.push(base.x + w * (x - end.x) + v * (dx - end.tx * sign),
        base.z + w * (z - end.z) + v * (dz - end.tz * sign));
      h.push(base.y + (a.heights && b.heights ? w * (y - end.y) : 0));
    }
    const points = Float64Array.from(p);
    return { points, cumulative: arcLengths(points), heights: line.heights ? Float64Array.from(h) : null };
  };
  return [bend(a, aStart, tx, tz), bend(b, bStart, -tx, -tz)];
}
