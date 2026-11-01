// Resident tiles' traffic lanes, linked across seams, and the closed-form car positions on them.
//
// A car is a slot (lane, n): it enters `lane` at phase + n·H and follows the lane's matched successors. Its
// position at shared time t is a pure function of t and the tiles' road data; nothing is simulated or sent.
// The timetable has corners (stops at junctions, speed changes); a box filter of half-width T over the
// timetable's distance turns them into braking and pulling away while leaving straight cruising exact, and it
// preserves the headway between consecutive cars of a chain, so they still cannot overlap.
import { TRAFFIC } from './constants';
import { mod } from './hash';
import { laneSpan, type Connector, type Lane, type TileTraffic } from './tileNetwork';

const H = TRAFFIC.headway;

export interface CarPose {
  x: number; y: number; z: number;
  /** Unit heading (x, z) and pitch (rad, + nose up). */
  hx: number; hz: number; pitch: number;
  /** Speed along the route (m/s). */
  speed: number;
}

/** Height of the road surface along a lane (arc length s); null where unknown (no collision yet). */
export type LaneHeight = (lane: Lane, s: number) => number | null;

interface Segment { r0: number; r1: number; points: Float64Array; cum: Float64Array; lane: Lane | null; from: Lane | null; to: Lane | null }

/** Entry time of slot n. */
export const entryTime = (lane: Lane, n: number) => lane.phase + n * H;

export class TrafficNetwork {
  readonly tiles = new Map<string, TileTraffic>();
  private enabled: ReadonlySet<string> | null = null;
  /** Bumped whenever lanes or links change (callers cache per-lane data against it). */
  revision = 0;

  setTile(tile: TileTraffic) { this.tiles.set(tile.tile, tile); this.relink(); }
  removeTile(id: string) { if (this.tiles.delete(id)) this.relink(); }
  /** Tiles whose lanes may carry cars (null: all resident tiles). */
  setEnabled(tiles: ReadonlySet<string> | null) { this.enabled = tiles; this.revision++; }
  isEnabled(tile: string) { return this.tiles.has(tile) && (!this.enabled || this.enabled.has(tile)); }

  /** Joins lanes that end on a seam to the neighbouring tile's lanes that start at the same crossing. */
  private relink() {
    this.revision++;
    const ends = new Map<string, Lane[]>(), starts = new Map<string, Lane[]>();
    const add = (map: Map<string, Lane[]>, key: string, lane: Lane) => { const list = map.get(key); if (list) list.push(lane); else map.set(key, [lane]); };
    for (const tile of this.tiles.values()) for (const lane of tile.lanes) {
      lane.seamNext = null; lane.seamPrev = null;
      const end = lane.end.seam, start = lane.start.seam;
      if (end) add(ends, `${end.line}|${end.dir}|${lane.k}`, lane);
      if (start) add(starts, `${start.line}|${start.dir}|${lane.k}`, lane);
    }
    for (const [key, list] of ends) {
      const candidates = starts.get(key);
      if (!candidates) continue;
      const pairs: { d: number; a: Lane; b: Lane }[] = [];
      for (const a of list) for (const b of candidates) {
        const d = Math.abs(a.end.seam!.u - b.start.seam!.u);
        if (b.tile !== a.tile && d < TRAFFIC.seamMatch) pairs.push({ d, a, b });
      }
      pairs.sort((p, q) => p.d - q.d || (p.a.tile + p.a.id < q.a.tile + q.a.id ? -1 : 1));
      for (const { a, b } of pairs) if (!a.seamNext && !b.seamPrev) { a.seamNext = b; b.seamPrev = a; }
    }
  }

  /** The lane a car takes after `lane`: its junction match, or the next tile's lane across a seam. */
  successor(lane: Lane): Lane | null {
    const next = lane.next ?? lane.seamNext;
    return next && this.isEnabled(next.tile) ? next : null;
  }
  predecessor(lane: Lane): Lane | null {
    const prev = lane.prev ?? lane.seamPrev;
    return prev && this.isEnabled(prev.tile) ? prev : null;
  }

  /** Slot on the successor that a car of slot n continues as, or null where it leaves the network. */
  continuation(lane: Lane, n: number): { lane: Lane; n: number } | null {
    const next = this.successor(lane);
    if (!next) return null;
    return { lane: next, n: Math.round((entryTime(lane, n) + laneSpan(lane) - next.phase) / H) };
  }

  /** Slots whose car is on `lane` (or crossing its junction) at time t: [first, last], possibly empty. */
  slots(lane: Lane, t: number): [number, number] {
    const span = lane.next ? laneSpan(lane) : lane.tau;
    return [Math.floor((t - lane.phase - span) / H) + 1, Math.floor((t - lane.phase) / H)];
  }

  /** Identity of a car: the slot on its chain's first lane in this tile (stable while it stays in the tile). */
  key(lane: Lane, n: number): string {
    const n0 = n - lane.shift;
    return `${lane.tile}:${lane.origin.id}:${lane.cycle ? mod(n0, lane.cycle) : n0}`;
  }

  /** Where slot (lane, n) is at time t once it has moved on: null when it has left the network. */
  advance(lane: Lane, n: number, t: number): { lane: Lane; n: number } | null {
    let at: { lane: Lane; n: number } | null = { lane, n };
    for (let guard = 0; at && t >= entryTime(at.lane, at.n) + (at.lane.next ? laneSpan(at.lane) : at.lane.tau) && guard < 64; guard++) {
      if (!this.isEnabled(at.lane.tile)) return null;
      at = at.lane.next || at.lane.seamNext ? this.continuation(at.lane, at.n) : null;
    }
    return at && this.isEnabled(at.lane.tile) ? at : null;
  }

  /** Calls `visit` for every car within `radius` of (x, z) at time t, with its unsmoothed (timetable) position. */
  enumerate(x: number, z: number, radius: number, t: number, visit: (lane: Lane, n: number, px: number, pz: number) => void) {
    for (const tile of this.tiles.values()) {
      if (!this.isEnabled(tile.tile)) continue;
      const b = tile.bounds;
      if (x < b[0] - radius || x > b[2] + radius || z < b[1] - radius || z > b[3] + radius) continue;
      for (const lane of tile.lanes) {
        const l = lane.bbox;
        if (x < l[0] - radius || x > l[2] + radius || z < l[1] - radius || z > l[3] + radius) continue;
        const [first, last] = this.slots(lane, t);
        for (let n = first; n <= last; n++) {
          const r = timetable(lane, n, t);
          const p = pointOnLane(lane, r);
          if (Math.hypot(p.x - x, p.z - z) <= radius) visit(lane, n, p.x, p.z);
        }
      }
    }
  }

  /** The car's smoothed pose at time t; y is NaN where the height is unknown. */
  pose(lane: Lane, n: number, t: number, height: LaneHeight, out: CarPose = blankPose()): CarPose {
    const route = this.route(lane, n, t);
    const T = TRAFFIC.smoothing;
    const r = route.smooth ? integrate(route.knots, t - T, t + T) / (2 * T) : sampleKnots(route.knots, t);
    const speed = (sampleKnots(route.knots, t + T) - sampleKnots(route.knots, t - T)) / (2 * T);
    const front = locate(route.segments, r + 1.3), back = locate(route.segments, r - 1.3), mid = locate(route.segments, r);
    let hx = front.x - back.x, hz = front.z - back.z;
    const d = Math.hypot(hx, hz);
    if (d > 1e-6) { hx /= d; hz /= d; } else { hx = mid.hx; hz = mid.hz; }
    const yf = surface(front, height), yb = surface(back, height), ym = surface(mid, height);
    out.x = mid.x; out.z = mid.z; out.hx = hx; out.hz = hz; out.speed = Math.max(0, speed);
    out.y = Number.isFinite(yf) && Number.isFinite(yb) ? (yf + yb) / 2 : ym;
    out.pitch = Number.isFinite(yf) && Number.isFinite(yb) && d > 0.5 ? Math.atan2(yf - yb, d) : 0;
    return out;
  }

  /** The timetable around slot (lane, n) within the filter window of t: knots (time, route distance) and the
   *  geometry they run over. Route distance 0 is the start of `lane`. */
  private route(lane: Lane, n: number, t: number) {
    const T = TRAFFIC.smoothing;
    const knots: number[] = [], segments: Segment[] = [];
    const e = entryTime(lane, n);
    appendLane(knots, segments, lane, e, 0, this.successor(lane));
    // Backwards: predecessors until the window is covered.
    let first = lane, firstEntry = e, r0 = 0;
    for (let guard = 0; knots[0] > t - T && guard < 6; guard++) {
      const prev = this.predecessor(first);
      if (!prev) break;
      const span = laneSpan(prev);
      const np = Math.round((firstEntry - prev.phase - span) / H), ep = entryTime(prev, np);
      const pk: number[] = [], ps: Segment[] = [];
      const c = prev.connector?.length ?? 0;
      appendLane(pk, ps, prev, ep, r0 - c - prev.length, first);
      // Its last knot coincides with our first: drop the duplicate.
      knots.unshift(...pk.slice(0, pk.length - 2));
      segments.unshift(...ps);
      first = prev; firstEntry = ep; r0 -= c + prev.length;
    }
    // Forwards: successors until the window is covered.
    let last = lane, lastEntry = e;
    let r1 = lane.length + (lane.connector && this.successor(lane) === lane.next ? lane.connector.length : 0);
    for (let guard = 0; knots[knots.length - 2] < t + T && guard < 6; guard++) {
      const next = this.successor(last);
      if (!next) break;
      const en = lastEntry + laneSpan(last);
      const nn = Math.round((en - next.phase) / H), ent = entryTime(next, nn);
      const nk: number[] = [];
      appendLane(nk, segments, next, ent, r1, this.successor(next));
      knots.push(...nk.slice(2));
      last = next; lastEntry = ent;
      r1 += next.length + (next.connector && this.successor(next) === next.next ? next.connector.length : 0);
    }
    let smooth = false;
    for (let i = 0; i < knots.length; i += 2) if (knots[i] > t - T && knots[i] < t + T) { smooth = true; break; }
    return { knots, segments, smooth };
  }
}

export function blankPose(): CarPose { return { x: 0, y: 0, z: 0, hx: 1, hz: 0, pitch: 0, speed: 0 }; }

/** Knots of one lane's timetable for a car entering at time e at route distance r: drive, wait, cross. */
function appendLane(knots: number[], segments: Segment[], lane: Lane, e: number, r: number, next: Lane | null) {
  const crossing = lane.connector && next === lane.next;
  knots.push(e, r, e + lane.tau, r + lane.length);
  segments.push({ r0: r, r1: r + lane.length, points: lane.points, cum: lane.cum, lane, from: null, to: null });
  if (crossing) {
    const c = lane.connector as Connector;
    knots.push(e + lane.tau + lane.wait, r + lane.length, e + laneSpan(lane), r + lane.length + c.length);
    segments.push({ r0: r + lane.length, r1: r + lane.length + c.length, points: c.points, cum: c.cum, lane: null, from: lane, to: next });
  }
}

/** Route distance of slot n on its own lane at time t (timetable, no smoothing). */
function timetable(lane: Lane, n: number, t: number): number {
  const u = t - entryTime(lane, n);
  if (u <= lane.tau) return u * lane.vEff;
  if (!lane.connector || u <= lane.tau + lane.wait) return lane.length;
  return lane.length + Math.min(lane.connector.length, (u - lane.tau - lane.wait) / Math.max(1e-6, lane.delta) * lane.connector.length);
}

function pointOnLane(lane: Lane, r: number): { x: number; z: number } {
  if (r <= lane.length || !lane.connector) return pointAt(lane.points, lane.cum, Math.min(r, lane.length));
  return pointAt(lane.connector.points, lane.connector.cum, r - lane.length);
}

/** Piecewise-linear knots (t0, r0, t1, r1, ...) evaluated at t, extended linearly beyond both ends. */
function sampleKnots(k: number[], t: number): number {
  const n = k.length / 2;
  if (n === 1) return k[1];
  if (t <= k[0]) return k[1] + (t - k[0]) * slope(k, 0);
  if (t >= k[2 * n - 2]) return k[2 * n - 1] + (t - k[2 * n - 2]) * slope(k, n - 2);
  let i = 0;
  while (i < n - 2 && k[2 * i + 2] < t) i++;
  const dt = k[2 * i + 2] - k[2 * i];
  return dt > 1e-9 ? k[2 * i + 1] + (k[2 * i + 3] - k[2 * i + 1]) * (t - k[2 * i]) / dt : k[2 * i + 3];
}

/** Slope of interval i, skipping zero-length and waiting intervals at the ends (a car outside the timetable
 *  keeps cruising). */
function slope(k: number[], i: number): number {
  const n = k.length / 2;
  for (let step = 0; step < n - 1; step++) {
    const j = i === 0 ? step : n - 2 - step;
    const dt = k[2 * j + 2] - k[2 * j], dr = k[2 * j + 3] - k[2 * j + 1];
    if (dt > 1e-9 && dr > 1e-9) return dr / dt;
  }
  return 0;
}

/** ∫ r(t) dt over [a, b] of the knots (linear extension beyond them). */
function integrate(k: number[], a: number, b: number): number {
  const n = k.length / 2;
  const pts: number[] = [a, sampleKnots(k, a)];
  for (let i = 0; i < n; i++) if (k[2 * i] > a && k[2 * i] < b) pts.push(k[2 * i], k[2 * i + 1]);
  pts.push(b, sampleKnots(k, b));
  let sum = 0;
  for (let i = 2; i < pts.length; i += 2) sum += (pts[i] - pts[i - 2]) * (pts[i + 1] + pts[i - 1]) / 2;
  return sum;
}

interface Located { x: number; z: number; hx: number; hz: number; seg: Segment | null; s: number }

function locate(segments: Segment[], r: number): Located {
  let seg = segments[0];
  for (const candidate of segments) { seg = candidate; if (r <= candidate.r1) break; }
  const s = r - seg.r0, length = seg.r1 - seg.r0;
  const clamped = Math.max(0, Math.min(length, s));
  const p = pointAt(seg.points, seg.cum, clamped);
  // Beyond the timetable's geometry: continue straight along the end tangent.
  const over = s - clamped;
  return { x: p.x + p.hx * over, z: p.z + p.hz * over, hx: p.hx, hz: p.hz, seg, s: clamped };
}

function surface(at: Located, height: LaneHeight): number {
  const seg = at.seg;
  if (!seg) return NaN;
  if (seg.lane) return height(seg.lane, at.s) ?? NaN;
  // A connector blends from its lane's stop line to the next lane's start.
  const a = seg.from ? height(seg.from, seg.from.length) : null, b = seg.to ? height(seg.to, 0) : null;
  if (a === null || b === null) return a ?? b ?? NaN;
  const f = seg.r1 > seg.r0 ? at.s / (seg.r1 - seg.r0) : 0;
  return a + (b - a) * f;
}

/** Point and unit tangent at arc length s along a polyline. */
export function pointAt(p: Float64Array, cum: Float64Array, s: number): { x: number; z: number; hx: number; hz: number } {
  const n = p.length / 2;
  let lo = 1, hi = n - 1;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (cum[mid] < s) lo = mid + 1; else hi = mid; }
  const i = Math.max(1, lo);
  const span = cum[i] - cum[i - 1];
  const f = span > 1e-9 ? Math.min(1, Math.max(0, (s - cum[i - 1]) / span)) : 0;
  const dx = p[2 * i] - p[2 * i - 2], dz = p[2 * i + 1] - p[2 * i - 1], d = Math.hypot(dx, dz) || 1;
  return { x: p[2 * i - 2] + dx * f, z: p[2 * i - 1] + dz * f, hx: dx / d, hz: dz / d };
}
