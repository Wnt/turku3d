// A drivable or walkable path for the autopilot: an x,z polyline in engine metres with an optional surface Y per
// vertex, its cumulative arc length, and the projection and curvature queries the followers need.
export interface PathPoint { x: number; z: number; y?: number }

export interface Path {
  /** x,z pairs in travel order. */
  points: Float64Array;
  /** Surface Y per vertex, or null when unknown. */
  ys: Float64Array | null;
  /** Cumulative arc length per vertex; the last value is the length. */
  cum: Float64Array;
  length: number;
}

export function makePath(points: readonly PathPoint[]): Path {
  const kept: PathPoint[] = [];
  for (const p of points) {
    const last = kept.at(-1);
    if (!last || Math.hypot(p.x - last.x, p.z - last.z) > 0.05) kept.push(p);
  }
  const xz = new Float64Array(kept.length * 2), cum = new Float64Array(kept.length);
  const known = kept.every(p => p.y !== undefined && Number.isFinite(p.y));
  const ys = known && kept.length ? Float64Array.from(kept, p => p.y!) : null;
  kept.forEach((p, i) => {
    xz[2 * i] = p.x; xz[2 * i + 1] = p.z;
    if (i) cum[i] = cum[i - 1] + Math.hypot(p.x - kept[i - 1].x, p.z - kept[i - 1].z);
  });
  return { points: xz, ys, cum, length: kept.length ? cum[kept.length - 1] : 0 };
}

export function pathPoints(path: Path): PathPoint[] {
  return Array.from({ length: path.cum.length }, (_, i) => ({ x: path.points[2 * i], z: path.points[2 * i + 1],
    ...(path.ys ? { y: path.ys[i] } : {}) }));
}

/** Segment index i with cum[i] <= s < cum[i+1] (clamped). */
function segmentAt(path: Path, s: number): number {
  const c = path.cum;
  let lo = 0, hi = c.length - 1;
  if (hi < 1) return 0;
  if (s <= 0) return 0;
  if (s >= c[hi]) return hi - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (c[mid] <= s) lo = mid; else hi = mid; }
  return lo;
}

export interface PathSample { x: number; z: number; y: number | null; hx: number; hz: number }

/** Point, surface Y and unit heading at arc length s (clamped to the path). */
export function sampleAt(path: Path, s: number, out: PathSample = { x: 0, z: 0, y: null, hx: 1, hz: 0 }): PathSample {
  const n = path.cum.length;
  if (n === 1) { out.x = path.points[0]; out.z = path.points[1]; out.y = path.ys?.[0] ?? null; return out; }
  const i = segmentAt(path, s), p = path.points, c = path.cum;
  const len = c[i + 1] - c[i] || 1e-9, t = Math.min(1, Math.max(0, (s - c[i]) / len));
  const dx = p[2 * i + 2] - p[2 * i], dz = p[2 * i + 3] - p[2 * i + 1], d = Math.hypot(dx, dz) || 1;
  out.x = p[2 * i] + dx * t; out.z = p[2 * i + 1] + dz * t;
  out.y = path.ys ? path.ys[i] + (path.ys[i + 1] - path.ys[i]) * t : null;
  out.hx = dx / d; out.hz = dz / d;
  return out;
}

export interface Projection { s: number; distance: number; x: number; z: number; /** + right of travel */ lateral: number }

/** Nearest point on the path to (x, z), searching arc lengths [from, to] (whole path by default). With a surface
 *  height `y`, vertices more than `levelTolerance` metres above or below are skipped: a road passing under a
 *  bridge deck never captures a car on the deck. */
export function project(path: Path, x: number, z: number, from = 0, to = Infinity, y?: number, levelTolerance = 4): Projection {
  const p = path.points, c = path.cum, n = c.length;
  let best: Projection = { s: 0, distance: Infinity, x: p[0], z: p[1], lateral: 0 };
  if (n === 1) return { s: 0, distance: Math.hypot(x - p[0], z - p[1]), x: p[0], z: p[1], lateral: 0 };
  const i0 = segmentAt(path, from), i1 = to === Infinity ? n - 2 : segmentAt(path, to);
  for (let i = i0; i <= i1; i++) {
    if (y !== undefined && path.ys && Math.min(Math.abs(path.ys[i] - y), Math.abs(path.ys[i + 1] - y)) > levelTolerance) continue;
    const x0 = p[2 * i], z0 = p[2 * i + 1], dx = p[2 * i + 2] - x0, dz = p[2 * i + 3] - z0;
    const len2 = dx * dx + dz * dz;
    const t = len2 > 0 ? Math.min(1, Math.max(0, ((x - x0) * dx + (z - z0) * dz) / len2)) : 0;
    const px = x0 + dx * t, pz = z0 + dz * t, distance = Math.hypot(x - px, z - pz);
    if (distance < best.distance) {
      const len = Math.sqrt(len2) || 1;
      best = { s: c[i] + t * len, distance, x: px, z: pz, lateral: ((x - x0) * -dz + (z - z0) * dx) / len };
    }
  }
  return best;
}

/** Signed curvature (1/m, + right turn) at arc length s, from the heading change over ±half metres. */
export function curvatureAt(path: Path, s: number, half = 3): number {
  const a = sampleAt(path, Math.max(0, s - half)), b = sampleAt(path, Math.min(path.length, s + half));
  const span = Math.min(path.length, s + half) - Math.max(0, s - half);
  if (span < 0.5) return 0;
  // Heading of the chord before and after s: robust on polylines with short segments.
  const m = sampleAt(path, s);
  const ax = m.x - a.x, az = m.z - a.z, bx = b.x - m.x, bz = b.z - m.z;
  const la = Math.hypot(ax, az), lb = Math.hypot(bx, bz);
  if (la < 0.2 || lb < 0.2) return 0;
  const cross = (ax * bz - az * bx) / (la * lb), dot = (ax * bx + az * bz) / (la * lb);
  // Engine x east, z south: a positive x/z cross product turns clockwise seen from above, i.e. right.
  return Math.atan2(cross, dot) / (span / 2);
}

/** Heading change (rad) of a path between two arc lengths, to tell lane-level turns from straights. */
export function turnBetween(path: Path, s0: number, s1: number): number {
  const a = sampleAt(path, s0), b = sampleAt(path, s1);
  return Math.atan2(a.hx * b.hz - a.hz * b.hx, a.hx * b.hx + a.hz * b.hz);
}
