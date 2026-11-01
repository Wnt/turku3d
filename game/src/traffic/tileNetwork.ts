// One tile's traffic lanes, built only from that tile's own roads.json (traffic/roads.ts). Nothing here reads
// another tile, the merged graph or the load order, so every client that holds the same tile data builds the
// same lanes, phases and identities (the multiplayer gate in traffic/gate.ts makes sure they hold the same data).
//
// The conveyor model (street-richness plan §3.6, extended so cars turn instead of vanishing at every lane end):
// - A lane is a stretch of street between two junctions, seams or dead ends, in one direction.
// - Slot n of lane L enters it at time phase(L) + n·H; H is one global headway, so every lane is a conveyor of
//   cars H seconds apart. A car cruises the lane, waits `wait` at its stop line and crosses the junction on a
//   connector curve onto its matched successor, entering exactly on that lane's own slot grid: phases lock.
// - Each junction matches in-lanes to out-lanes one to one with a turn preference and a hash, so a chain of
//   matched lanes never merges: cars following each other keep the headway and cannot overlap.
// - Out-lane phases at a junction are staggered by H/k so cars from different approaches enter apart.
// - A lane ending at a tile seam meets a phase that is a pure function of the seam crossing point; the next tile
//   computes the same value from its own copy of the crossing, so cars cross seams without either tile knowing
//   the other. Lanes start pinned to it where possible (junction→seam) or slow slightly (seam→seam).
// - Every lane has a peak flow (traffic/density.ts). Junction matching prefers lanes of similar flow, so a chain
//   mostly stays on one kind of street, and each chain names the anchor its cars' identities hash from
//   (TrafficNetwork.present): the seam it enters or leaves the tile by, so a car keeps its presence across the
//   seam, or else its origin lane.
import { TRAFFIC } from './constants';
import { hash01, hash32, hex32, mod } from './hash';
import type { TrafficEdge, TrafficRoads } from './roads';

export type Bounds = readonly [number, number, number, number];

export interface SeamKey {
  /** Seam line: `x:<X>` for a vertical seam at x = X, `z:<Z>` for a horizontal one. */
  line: string; vertical: boolean; coord: number;
  /** Position of the crossing along the seam (m) and the travel sign across it (+1 toward +x / +z). */
  u: number; dir: 1 | -1;
}

export interface LaneEnd {
  kind: 'seam' | 'junction';
  node: number;
  seam: SeamKey | null;
}

export interface Connector { points: Float64Array; cum: Float64Array; length: number }

export interface Lane {
  tile: string;
  /** Stable 8-hex id within the tile. */
  id: string;
  index: number;
  /** Lane number within its direction (0 = kerb side). */
  k: number;
  /** x,z pairs in travel order, offset from the centreline and trimmed at junctions. */
  points: Float64Array;
  cum: Float64Array;
  length: number;
  /** Surface Y per point when the road data has it, else null (the worker samples collision). */
  ys: Float64Array | null;
  speed: number;
  /** Vehicles per hour at the busiest hour (traffic/density.ts). */
  flow: number;
  /** Effective speed: the cruise speed, except seam-to-seam lanes that slow to meet the next tile's phase. */
  vEff: number;
  /** Driving time (length / vEff). */
  tau: number;
  start: LaneEnd; end: LaneEnd;
  /** Slot 0 entry time modulo the headway (s). */
  phase: number;
  /** Matched successor at the end junction (same tile), its connector, the stop-line wait and the crossing time. */
  next: Lane | null; connector: Connector | null; wait: number; delta: number;
  prev: Lane | null;
  /** Cross-tile neighbours at seams (set by TrafficNetwork). */
  seamNext: Lane | null; seamPrev: Lane | null;
  /** Identity: the first lane of this lane's chain within the tile, the slot offset to it, and, for a closed
   *  chain, the number of cars on it (origin slots repeat modulo this). */
  origin: Lane; shift: number; cycle: number;
  /** Where a car's presence is decided (TrafficNetwork.present): slot m of `phase + m·H` at the anchor `key`, which
   *  the car reaches `offset` seconds after entering the origin lane. */
  anchor: Anchor;
  /** x0, z0, x1, z1 of the lane and its connector. */
  bbox: [number, number, number, number];
}

export interface Anchor { key: string; phase: number; offset: number }

export interface TileTraffic {
  tile: string;
  /** Agreed road hash (traffic/gate.ts). */
  hash: string;
  bounds: Bounds;
  lanes: Lane[];
  byId: Map<string, Lane>;
}

/** Time from entering a lane to entering the next one (driving, waiting, crossing). */
export const laneSpan = (lane: Lane) => lane.tau + lane.wait + lane.delta;

/** Phase of a seam crossing: continuous along the seam, so both tiles' copies (to 0.1 m) agree within ~20 ms. */
export function seamPhase(s: SeamKey): number {
  return mod(s.u / 37.3 + s.coord * 0.00618 + (s.dir > 0 ? 0 : 0.5) + (s.vertical ? 0 : 0.25), 1) * TRAFFIC.headway;
}

interface Stretch {
  /** Node sequence start → end and the centreline. */
  start: number; end: number;
  points: number[]; ys: (number | null)[];
  forward: number; backward: number;
  /** Carriageway width (m) and junction setbacks at the start and end node (0 when the data has none). */
  width: number; setStart: number; setEnd: number;
  speed: number;
  /** Lowest peak flow of its edges. */
  flow: number;
  /** Edge keys in start → end order. */
  keys: string[];
}

export function buildTileTraffic(tile: string, roads: TrafficRoads, bounds: Bounds, hash: string): TileTraffic {
  const H = TRAFFIC.headway;
  const { nodes, edges } = roads;
  const count = nodes.length / 2;
  const incident: number[][] = Array.from({ length: count }, () => []);
  edges.forEach((e, i) => { incident[e.a].push(i); if (e.b !== e.a) incident[e.b].push(i); });
  const tol = TRAFFIC.seamTolerance;
  const seamOf = (n: number): { vertical: boolean; coord: number } | null => {
    const x = nodes[2 * n], z = nodes[2 * n + 1];
    const dx = Math.min(Math.abs(x - bounds[0]), Math.abs(x - bounds[2]));
    const dz = Math.min(Math.abs(z - bounds[1]), Math.abs(z - bounds[3]));
    // Revision 2 marks seam nodes itself; the bbox only tells which seam line.
    if (roads.seams ? !roads.seams.has(n) || Math.min(dx, dz) > 0.5 : dx >= tol && dz >= tol) return null;
    if (dx <= dz) return { vertical: true, coord: Math.abs(x - bounds[0]) < Math.abs(x - bounds[2]) ? bounds[0] : bounds[2] };
    return { vertical: false, coord: Math.abs(z - bounds[1]) < Math.abs(z - bounds[3]) ? bounds[1] : bounds[3] };
  };
  const into = (e: TrafficEdge, n: number) => (e.b === n ? e.forward : 0) + (e.a === n ? e.backward : 0);
  const out = (e: TrafficEdge, n: number) => (e.a === n ? e.forward : 0) + (e.b === n ? e.backward : 0);
  const forced = new Set<number>();
  const kind = (n: number): 'seam' | 'through' | 'junction' => {
    const list = incident[n];
    if (list.length === 1 && seamOf(n)) return 'seam';
    if (list.length === 2 && !forced.has(n) && !seamOf(n) && !roads.signals.has(n)) {
      const [e1, e2] = [edges[list[0]], edges[list[1]]];
      if (e1.a !== e1.b && e2.a !== e2.b && into(e1, n) === out(e2, n) && into(e2, n) === out(e1, n)) return 'through';
    }
    return 'junction';
  };

  // Stretches: chains of edges through plain degree-2 nodes.
  const visited = new Set<number>();
  const stretches: Stretch[] = [];
  const walk = (from: number, first: number) => {
    const s: Stretch = { start: from, end: from, points: [], ys: [], forward: 0, backward: 0, width: 0, setStart: 0, setEnd: 0, speed: Infinity,
      flow: Infinity, keys: [] };
    let node = from, e = first;
    for (;;) {
      visited.add(e);
      const edge = edges[e];
      const forward = edge.a === node;
      const n = edge.points.length / 2;
      if (!s.keys.length) {
        s.forward = forward ? edge.forward : edge.backward; s.backward = forward ? edge.backward : edge.forward;
        s.width = edge.width; s.setStart = forward ? edge.setbackA : edge.setbackB;
      }
      s.setEnd = forward ? edge.setbackB : edge.setbackA;
      for (let i = s.points.length ? 1 : 0; i < n; i++) {
        const k = forward ? i : n - 1 - i;
        s.points.push(edge.points[2 * k], edge.points[2 * k + 1]);
        s.ys.push(edge.ys ? edge.ys[k] : null);
      }
      s.keys.push(edge.key);
      s.speed = Math.min(s.speed, edge.speed);
      s.flow = Math.min(s.flow, edge.flow);
      node = forward ? edge.b : edge.a;
      s.end = node;
      if (node === from || kind(node) !== 'through') break;
      const next = incident[node].find(other => other !== e && !visited.has(other));
      if (next === undefined) break;
      e = next;
    }
    stretches.push(s);
  };
  for (let n = 0; n < count; n++) {
    if (!incident[n].length || kind(n) === 'through') continue;
    for (const e of incident[n]) if (!visited.has(e)) walk(n, e);
  }
  // Rings of plain nodes only (a roundabout inside the tile): anchor each at its lowest node and walk it.
  for (let e = 0; e < edges.length; e++) {
    if (visited.has(e)) continue;
    const { a, b } = edges[e];
    const anchor = nodes[2 * a] < nodes[2 * b] || (nodes[2 * a] === nodes[2 * b] && nodes[2 * a + 1] <= nodes[2 * b + 1]) ? a : b;
    forced.add(anchor);
    walk(anchor, e);
  }

  const endOf = (n: number, tx: number, tz: number): LaneEnd => {
    if (kind(n) !== 'seam') return { kind: 'junction', node: n, seam: null };
    const seam = seamOf(n)!;
    const u = seam.vertical ? nodes[2 * n + 1] : nodes[2 * n];
    const dir = (seam.vertical ? tx : tz) >= 0 ? 1 : -1;
    return { kind: 'seam', node: n, seam: { line: `${seam.vertical ? 'x' : 'z'}:${seam.coord}`, vertical: seam.vertical, coord: seam.coord, u, dir } };
  };
  /** Stop short of a junction node by the data's setback (revision 2), else by the default trim. */
  const trimAt = (n: number, setback: number) => kind(n) === 'seam' ? 0 : incident[n].length === 1 ? TRAFFIC.deadEndTrim
    : setback > 0 ? Math.max(2, setback) : TRAFFIC.junctionTrim;

  const lanes: Lane[] = [];
  const used = new Set<string>();
  for (const s of stretches) {
    for (const dir of [1, -1] as const) {
      const m = dir > 0 ? s.forward : s.backward;
      if (!m) continue;
      // Lane centres as roads-json-v2 §3.4: the divider sits at -width/2 + B·w (w = width / (F + B)) along the
      // stretch's right-hand normal; lanes count outward from it. 1+1 drives at ±width/4, a one-way lane on the
      // centreline. Seen in the backward direction the normal flips, and so does the divider's offset.
      const w = s.width / (s.forward + s.backward), divider = -s.width / 2 + s.backward * w;
      const centre = dir > 0 ? s.points : reversePairs(s.points);
      const ys = s.ys.every(y => y !== null) ? Float64Array.from(dir > 0 ? s.ys as number[] : [...s.ys as number[]].reverse()) : null;
      const startNode = dir > 0 ? s.start : s.end, endNode = dir > 0 ? s.end : s.start;
      const keys = dir > 0 ? s.keys : [...s.keys].reverse();
      for (let k = 0; k < m; k++) {
        const offset = (dir > 0 ? divider : -divider) + (k + 0.5) * w;
        const shifted = offsetLine(centre, offset);
        const full = polylineLength(shifted);
        if (full < 0.5) continue;
        const r0 = Math.min(trimAt(startNode, dir > 0 ? s.setStart : s.setEnd), full * 0.35);
        const r1 = Math.min(trimAt(endNode, dir > 0 ? s.setEnd : s.setStart), full * 0.35);
        const { points, ys: trimmedYs } = trim(shifted, ys, r0, full - r1);
        const cum = cumulative(points);
        const length = cum[cum.length - 1];
        if (length < 0.3) continue;
        let id = hex32(hash32('lane', keys.join('|'), k));
        for (let salt = 1; used.has(id); salt++) id = hex32(hash32(id, salt));
        used.add(id);
        const n = points.length / 2;
        const start = endOf(startNode, points[2] - points[0], points[3] - points[1]);
        const end = endOf(endNode, points[2 * n - 2] - points[2 * n - 4], points[2 * n - 1] - points[2 * n - 3]);
        const lane: Lane = { tile, id, index: lanes.length, k, points, cum, length, ys: trimmedYs, speed: s.speed, flow: s.flow,
          vEff: s.speed, tau: length / s.speed, start, end, phase: 0, next: null, connector: null, wait: 0, delta: 0, prev: null,
          seamNext: null, seamPrev: null, origin: null as unknown as Lane, shift: 0, cycle: 0, anchor: NO_ANCHOR, bbox: bboxOf(points) };
        lanes.push(lane);
      }
    }
  }
  // Ids are hashes, so sort for every per-junction decision; lane indices stay in build order.
  const byId = new Map(lanes.map(l => [l.id, l]));

  // Junction matching: in-lanes to out-lanes one to one, preferring straight on, then right, then left turns, and
  // a street of similar flow: main-road chains keep to main roads, and a living street's few cars turn back at
  // the main road rather than main-road cars turning in (each doubling of flow costs about a right turn's worth).
  const ins = new Map<number, Lane[]>(), outs = new Map<number, Lane[]>();
  for (const lane of lanes) {
    if (lane.end.kind === 'junction') push(ins, lane.end.node, lane);
    if (lane.start.kind === 'junction') push(outs, lane.start.node, lane);
  }
  for (const [node, list] of ins) {
    const options = outs.get(node) ?? [];
    const deadEnd = incident[node].length === 1;
    const pairs: { cost: number; a: Lane; b: Lane }[] = [];
    for (const a of list) for (const b of options) {
      const [ax, az] = tangentAtEnd(a), [bx, bz] = tangentAtStart(b);
      const theta = Math.atan2(ax * bz - az * bx, ax * bx + az * bz);
      const uTurn = Math.abs(theta) > 2.6;
      let cost = Math.abs(theta) < 0.5 ? 0 : theta > 0 ? 0.35 : 0.5;
      if (uTurn) cost = deadEnd ? 0 : 4;
      else if (Math.abs(theta) > 2.0) cost = 1.5;
      cost += MISMATCH * Math.abs(Math.log2(Math.max(1, a.flow) / Math.max(1, b.flow)));
      pairs.push({ cost: cost + 0.8 * hash01('turn', tile, node, a.id, b.id), a, b });
    }
    pairs.sort((p, q) => p.cost - q.cost || cmp(p.a.id, q.a.id) || cmp(p.b.id, q.b.id));
    for (const { a, b } of pairs) {
      if (a.next || b.prev) continue;
      a.next = b; b.prev = a;
      a.connector = connector(a, b);
    }
  }

  // Phases. Seam starts take the seam's phase; a lane from a junction to a seam is pinned back from the seam.
  // At a junction the out-lanes split into two windows of the headway by approach axis, so crossing streams
  // take turns (a signal controller's approaches likewise); within a window they are staggered. The window
  // grid is placed so the junction's main straight-on movement (fastest street) waits zero: main roads flow
  // in a green wave and side streets give way. Junctions are resolved once their main in-lane has a phase;
  // a ring of junctions without a start takes a hashed base at its lowest node.
  for (const lane of lanes) {
    if (lane.start.kind === 'seam') lane.phase = seamPhase(lane.start.seam!);
    else if (lane.end.kind === 'seam') lane.phase = mod(seamPhase(lane.end.seam!) - lane.tau, H);
  }
  const known = new Set<Lane>(lanes.filter(l => l.start.kind === 'seam' || l.end.kind === 'seam'));
  const axis = (hx: number, hz: number) => mod(Math.atan2(hz, hx), Math.PI);
  interface Plan { node: number; free: Lane[]; main: Lane | null; group: Map<Lane, number> }
  const plans: Plan[] = [];
  for (const [node, list] of outs) {
    const free = list.filter(l => !known.has(l));
    if (!free.length) continue;
    const feeds = (ins.get(node) ?? []).filter(l => l.next && l.connector);
    const straight = feeds.filter(l => { const [ax, az] = tangentAtEnd(l), [bx, bz] = tangentAtStart(l.next!); return ax * bx + az * bz > Math.cos(0.5); });
    const main = straight.sort((a, b) => b.speed - a.speed || cmp(a.id, b.id))[0] ?? null;
    const reference = main ?? [...feeds].sort((a, b) => cmp(a.id, b.id))[0] ?? null;
    const theta0 = reference ? axis(...tangentAtEnd(reference)) : 0;
    const group = new Map<Lane, number>();
    for (const out of free) {
      const feed = out.prev;
      const [hx, hz] = feed ? tangentAtEnd(feed) : tangentAtStart(out);
      group.set(out, mod(axis(hx, hz) - theta0 + Math.PI / 4, Math.PI) < Math.PI / 2 ? 0 : 1);
    }
    plans.push({ node, free: free.sort((a, b) => cmp(a.id, b.id)), main: main && free.includes(main.next!) ? main : null, group });
  }
  const slot = (plan: Plan, out: Lane) => {
    const g = plan.group.get(out)!, members = plan.free.filter(l => plan.group.get(l) === g);
    return g * H / 2 + members.indexOf(out) * (H / 2) / members.length;
  };
  const resolve = (plan: Plan, base: number) => {
    for (const out of plan.free) { out.phase = mod(base + slot(plan, out), H); known.add(out); }
  };
  const pending = new Set(plans);
  while (pending.size) {
    let progress = false;
    for (const plan of [...pending]) {
      if (!plan.main || !known.has(plan.main)) continue;
      const m = plan.main, arrival = m.phase + m.tau + m.connector!.length / m.next!.speed;
      resolve(plan, arrival - slot(plan, m.next!));
      pending.delete(plan); progress = true;
    }
    if (progress) continue;
    const seed = [...pending].sort((a, b) => nodes[2 * a.node] - nodes[2 * b.node] || nodes[2 * a.node + 1] - nodes[2 * b.node + 1])[0];
    // A signal controller's id seeds all of its junctions alike (roads-json-v2 §3.8).
    const controller = roads.signals.get(seed.node);
    resolve(seed, (controller ? hash01('controller', controller)
      : hash01('junction', tile, Math.round(nodes[2 * seed.node] * 2), Math.round(nodes[2 * seed.node + 1] * 2))) * H);
    pending.delete(seed);
  }
  for (const lane of lanes) {
    if (lane.start.kind === 'seam' && lane.end.kind === 'seam') {
      const slack = mod(seamPhase(lane.end.seam!) - lane.phase - lane.length / lane.speed, H);
      const v = lane.length / (lane.length / lane.speed + slack);
      // Too short to absorb the phase difference smoothly: this crossing does not continue (the cars of the
      // next tile appear on their own), rather than a car crawling across a tile corner.
      if (v >= TRAFFIC.minSpeedShare * lane.speed) { lane.vEff = v; lane.tau = lane.length / v; }
      else lane.end = { ...lane.end, seam: null };
    }
  }
  for (const lane of lanes) {
    if (!lane.next || !lane.connector) continue;
    lane.delta = lane.connector.length / lane.next.speed;
    const wait = mod(lane.next.phase - lane.delta - lane.phase - lane.tau, H);
    lane.wait = wait > H - 1e-6 ? 0 : wait;
    // The next car of the lane reaches the stop line H after this one did. A wait close to H would let it roll
    // up to a car that is only just pulling away: drive slower instead and keep a gap of `queueGap` seconds.
    const over = lane.wait - (H - TRAFFIC.queueGap);
    if (over > 0) { lane.tau += over; lane.vEff = lane.length / lane.tau; lane.wait -= over; }
  }

  // Identity: chains are simple paths or cycles (a matching). The origin is the chain's first lane, or the
  // lowest id on a cycle; `shift` converts a lane's slot to the origin's.
  const done = new Set<Lane>();
  for (const lane of lanes) {
    if (done.has(lane)) continue;
    let head = lane;
    const seen = new Set<Lane>([head]);
    let cyclic = false;
    while (head.prev) {
      if (seen.has(head.prev)) { cyclic = true; break; }
      head = head.prev; seen.add(head);
    }
    let chain: Lane[] = [];
    if (cyclic) {
      // The cycle is every lane reachable forward from `head` back to itself; start at its lowest id.
      const ring: Lane[] = [];
      let at: Lane | null = head;
      do { ring.push(at!); at = at!.next; } while (at && at !== head);
      const first = ring.reduce((low, l) => (cmp(l.id, low.id) < 0 ? l : low));
      const i0 = ring.indexOf(first);
      chain = [...ring.slice(i0), ...ring.slice(0, i0)];
    } else {
      for (let at: Lane | null = head; at && !done.has(at); at = at.next) chain.push(at);
    }
    const origin = chain[0], last = chain[chain.length - 1];
    let delta = 0;
    for (const l of chain) {
      l.origin = origin;
      l.shift = Math.round((delta - (l.phase - origin.phase)) / H);
      if (l !== last) delta += laneSpan(l);
      done.add(l);
    }
    if (cyclic) { const cars = Math.round((delta + laneSpan(last)) / H); for (const l of chain) l.cycle = Math.max(1, cars); }
    // The anchor: the seam the chain enters by, else the seam it leaves by (the neighbouring tile's chain from
    // that crossing anchors there too, so a car keeps its presence across the seam), else its origin lane.
    const anchor: Anchor = cyclic ? { key: `o|${tile}|${origin.id}`, phase: origin.phase, offset: 0 }
      : origin.start.seam ? { key: seamAnchor(origin.start.seam, origin.k), phase: seamPhase(origin.start.seam), offset: 0 }
      : last.end.seam ? { key: seamAnchor(last.end.seam, last.k), phase: seamPhase(last.end.seam), offset: delta + last.tau }
      : { key: `o|${tile}|${origin.id}`, phase: origin.phase, offset: 0 };
    for (const l of chain) l.anchor = anchor;
  }
  for (const lane of lanes) if (lane.connector) lane.bbox = bboxOf(lane.points, lane.connector.points);
  return { tile, hash, bounds, lanes, byId };
}

/** Matching cost per doubling of flow between an in-lane and an out-lane. */
const MISMATCH = 1.2;
const NO_ANCHOR: Anchor = { key: '', phase: 0, offset: 0 };
/** A seam crossing as both tiles name it: line, direction, lane and the crossing point to 8 m (their copies agree
 *  to 0.1 m; one that straddles a bucket edge only loses presence continuity there, never determinism). */
const seamAnchor = (s: SeamKey, k: number) => `s|${s.line}|${s.dir}|${k}|${Math.round(s.u / 8)}`;

const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
function push<K, V>(map: Map<K, V[]>, key: K, value: V) { const list = map.get(key); if (list) list.push(value); else map.set(key, [value]); }

function reversePairs(p: readonly number[]): number[] {
  const out: number[] = [];
  for (let i = p.length - 2; i >= 0; i -= 2) out.push(p[i], p[i + 1]);
  return out;
}

/** Offsets a polyline to the right of travel ((-dz, dx) in engine x/z), with mitred, clamped corners. */
function offsetLine(p: readonly number[], offset: number): number[] {
  const n = p.length / 2;
  // Drop repeated vertices so every segment has a direction.
  const xs: number[] = [], zs: number[] = [];
  for (let i = 0; i < n; i++) {
    if (xs.length && Math.hypot(p[2 * i] - xs[xs.length - 1], p[2 * i + 1] - zs[zs.length - 1]) < 1e-3) continue;
    xs.push(p[2 * i]); zs.push(p[2 * i + 1]);
  }
  const m = xs.length;
  if (m < 2) return [...p];
  const out: number[] = [];
  for (let i = 0; i < m; i++) {
    const a = Math.max(0, i - 1), b = Math.min(m - 1, i + 1);
    let nx = 0, nz = 0;
    for (const [s, e] of [[a, i], [i, b]]) {
      if (s === e) continue;
      const dx = xs[e] - xs[s], dz = zs[e] - zs[s], l = Math.hypot(dx, dz);
      nx += -dz / l; nz += dx / l;
    }
    const l = Math.hypot(nx, nz) || 1;
    nx /= l; nz /= l;
    // Mitre: scale by 1 / cos(half turn), clamped so a hairpin cannot throw the lane across the street.
    let scale = 1;
    if (i > 0 && i < m - 1) {
      const dx = xs[i] - xs[i - 1], dz = zs[i] - zs[i - 1], d = Math.hypot(dx, dz);
      const c = (-dz / d) * nx + (dx / d) * nz;
      scale = Math.min(2, 1 / Math.max(0.5, c));
    }
    out.push(xs[i] + nx * offset * scale, zs[i] + nz * offset * scale);
  }
  return out;
}

function polylineLength(p: readonly number[]) {
  let length = 0;
  for (let i = 2; i < p.length; i += 2) length += Math.hypot(p[i] - p[i - 2], p[i + 1] - p[i - 1]);
  return length;
}

export function cumulative(p: ArrayLike<number>): Float64Array {
  const n = p.length / 2, cum = new Float64Array(n);
  for (let i = 1; i < n; i++) cum[i] = cum[i - 1] + Math.hypot(p[2 * i] - p[2 * i - 2], p[2 * i + 1] - p[2 * i - 1]);
  return cum;
}

/** The part of a polyline between arc lengths s0 and s1 (with interpolated Y when given). */
function trim(p: readonly number[], ys: Float64Array | null, s0: number, s1: number): { points: Float64Array; ys: Float64Array | null } {
  const cum = cumulative(p), n = p.length / 2;
  const outP: number[] = [], outY: number[] = [];
  const at = (s: number) => {
    let i = 1;
    while (i < n - 1 && cum[i] < s) i++;
    const span = cum[i] - cum[i - 1] || 1, f = Math.min(1, Math.max(0, (s - cum[i - 1]) / span));
    outP.push(p[2 * i - 2] + (p[2 * i] - p[2 * i - 2]) * f, p[2 * i - 1] + (p[2 * i + 1] - p[2 * i - 1]) * f);
    if (ys) outY.push(ys[i - 1] + (ys[i] - ys[i - 1]) * f);
  };
  at(s0);
  for (let i = 1; i < n - 1; i++) if (cum[i] > s0 + 1e-3 && cum[i] < s1 - 1e-3) { outP.push(p[2 * i], p[2 * i + 1]); if (ys) outY.push(ys[i]); }
  at(s1);
  return { points: Float64Array.from(outP), ys: ys ? Float64Array.from(outY) : null };
}

function tangentAtEnd(l: Lane): [number, number] {
  const p = l.points, n = p.length / 2;
  const dx = p[2 * n - 2] - p[2 * n - 4], dz = p[2 * n - 1] - p[2 * n - 3], d = Math.hypot(dx, dz) || 1;
  return [dx / d, dz / d];
}
function tangentAtStart(l: Lane): [number, number] {
  const p = l.points, dx = p[2] - p[0], dz = p[3] - p[1], d = Math.hypot(dx, dz) || 1;
  return [dx / d, dz / d];
}

/** Cubic Bézier from the in-lane's stop line to the out-lane's start, tangent to both. */
function connector(a: Lane, b: Lane): Connector {
  const n = a.points.length / 2;
  const x0 = a.points[2 * n - 2], z0 = a.points[2 * n - 1], x3 = b.points[0], z3 = b.points[1];
  const [ax, az] = tangentAtEnd(a), [bx, bz] = tangentAtStart(b);
  const d = Math.hypot(x3 - x0, z3 - z0), k = Math.max(1.2, d * 0.4);
  const x1 = x0 + ax * k, z1 = z0 + az * k, x2 = x3 - bx * k, z2 = z3 - bz * k;
  const steps = 10, points = new Float64Array(2 * (steps + 1));
  for (let i = 0; i <= steps; i++) {
    const t = i / steps, u = 1 - t;
    points[2 * i] = u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3;
    points[2 * i + 1] = u * u * u * z0 + 3 * u * u * t * z1 + 3 * u * t * t * z2 + t * t * t * z3;
  }
  const cum = cumulative(points);
  return { points, cum, length: Math.max(0.01, cum[steps]) };
}

function bboxOf(...lists: ArrayLike<number>[]): [number, number, number, number] {
  const b: [number, number, number, number] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const p of lists) for (let i = 0; i < p.length; i += 2) {
    b[0] = Math.min(b[0], p[i]); b[1] = Math.min(b[1], p[i + 1]); b[2] = Math.max(b[2], p[i]); b[3] = Math.max(b[3], p[i + 1]);
  }
  return b;
}
