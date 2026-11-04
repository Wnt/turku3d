// Lane-level routing for the autopilot over the same lane graph the ambient cars drive: each tile's lanes from its
// own roads.json (traffic/tileNetwork.ts), joined at seams (traffic/network.ts), with the junction movements the
// traffic flow builds (traffic/flow.ts routesOf: lane discipline, folded crossing splinters, tangent arcs). A* runs
// over lanes; neighbouring lanes of one carriageway are joined by lane changes. Every client that holds the same
// tiles builds the same graph, so a route is the same in single player and multiplayer. Unlike ambient traffic it
// keeps every car edge (service roads and yards too), so lane ids are the router's own.
import { VISUAL_L0 } from '../../core/origin';
import type { RoadGraphJson } from '../roadGraph';
import { TrafficNetwork } from '../../traffic/network';
import { TrafficFlow } from '../../traffic/flow';
import { trafficRoads } from '../../traffic/roads';
import { buildTileTraffic, type Lane } from '../../traffic/tileNetwork';
import { makePath, project, type Path, type PathPoint } from './path';

export interface TileRoads { tile: string; json: RoadGraphJson; hash: string }
/** Fetches one tile's road graph, or null when the tile has none. */
export type RoadsLoader = (tile: string) => Promise<TileRoads | null>;

export interface LaneRef { tile: string; id: string }
export const laneKey = (lane: LaneRef) => `${lane.tile}:${lane.id}`;

export interface LaneRoute {
  path: Path;
  /** Lanes in driving order (lane changes and junction movements included). */
  lanes: LaneRef[];
  /** Posted speed limit (m/s) at arc length s along the path. */
  limit: (s: number) => number;
}

export interface RoutePoint { x: number; z: number; y?: number; /** Unit heading, when the start has one. */ hx?: number; hz?: number }

/** Lane-change cost in metres of driving: changing only when it shortens the route noticeably. */
const CHANGE_COST = 25;
/** A lane change takes this long along the lane (m), at most the lane's remaining length. */
const CHANGE_LENGTH = 28;
/** Ambient cruise is 0.85 of the limit (traffic/roads.ts LIMIT_SHARE); the autopilot drives at the limit. */
const LIMIT_SHARE = 0.85;
const tileOf = (x: number, z: number) => `t0_${Math.floor(x / VISUAL_L0)}_${Math.floor(z / VISUAL_L0)}`;
const tileBounds = (id: string): [number, number, number, number] => {
  const m = /^t0_(-?\d+)_(-?\d+)$/.exec(id)!;
  const i = Number(m[1]), j = Number(m[2]);
  return [i * VISUAL_L0, j * VISUAL_L0, (i + 1) * VISUAL_L0, (j + 1) * VISUAL_L0];
};

/** Tiles whose lanes a route between two points may use: the box around both plus `margin` tiles. */
export function tilesBetween(a: { x: number; z: number }, b: { x: number; z: number }, margin = 1, max = 64): string[] {
  const i0 = Math.floor(Math.min(a.x, b.x) / VISUAL_L0) - margin, i1 = Math.floor(Math.max(a.x, b.x) / VISUAL_L0) + margin;
  const j0 = Math.floor(Math.min(a.z, b.z) / VISUAL_L0) - margin, j1 = Math.floor(Math.max(a.z, b.z) / VISUAL_L0) + margin;
  const out: string[] = [];
  for (let i = i0; i <= i1; i++) for (let j = j0; j <= j1; j++) out.push(`t0_${i}_${j}`);
  // A long route keeps the tiles nearest the straight line between its ends.
  if (out.length <= max) return out;
  const dx = b.x - a.x, dz = b.z - a.z, len = Math.hypot(dx, dz) || 1;
  const off = (t: string) => {
    const [x0, z0, x1, z1] = tileBounds(t), cx = (x0 + x1) / 2 - a.x, cz = (z0 + z1) / 2 - a.z;
    return Math.abs(cx * -dz + cz * dx) / len;
  };
  return out.sort((p, q) => off(p) - off(q)).slice(0, max);
}

interface Step { lane: Lane; via: 'start' | 'move' | 'seam' | 'change'; route: Lane | null; from: Lane | null }

export class LaneRouter {
  readonly network = new TrafficNetwork();
  private flow: TrafficFlow | null = null;
  private readonly hashes = new Map<string, string>();
  private readonly pending = new Map<string, Promise<void>>();
  private incoming: Map<Lane, { from: Lane; route: Lane | null }[]> | null = null;
  private siblings: Map<string, Lane[]> | null = null;

  constructor(private readonly load: RoadsLoader | null = null) {}

  /** Adds or replaces one tile's lanes. */
  setTile(roads: TileRoads) {
    if (this.hashes.get(roads.tile) === roads.hash) return;
    this.network.setTile(buildTileTraffic(roads.tile, trafficRoads(roads.json, true), tileBounds(roads.tile), roads.hash));
    this.hashes.set(roads.tile, roads.hash);
    this.flow = null;
  }
  removeTile(tile: string) { if (this.hashes.delete(tile)) { this.network.removeTile(tile); this.flow = null; } }
  has(tile: string) { return this.hashes.has(tile); }
  get tiles(): string[] { return [...this.hashes.keys()]; }

  /** Loads the listed tiles' lanes (through the loader) that are not held yet. */
  async ensure(tiles: readonly string[]): Promise<void> {
    if (!this.load) return;
    await Promise.all(tiles.filter(t => !this.hashes.has(t)).map(tile => {
      let job = this.pending.get(tile);
      if (!job) {
        job = this.load!(tile).then(roads => { if (roads) this.setTile(roads); }, () => {}).finally(() => this.pending.delete(tile));
        this.pending.set(tile, job);
      }
      return job;
    }));
  }

  private graph(): TrafficFlow {
    if (this.flow) return this.flow;
    const flow = new TrafficFlow(this.network);
    flow.reset(0); // builds the movement graph (no cars matter here)
    const incoming = new Map<Lane, { from: Lane; route: Lane | null }[]>(), siblings = new Map<string, Lane[]>();
    const add = (to: Lane, from: Lane, route: Lane | null) => { const list = incoming.get(to); if (list) list.push({ from, route }); else incoming.set(to, [{ from, route }]); };
    for (const tile of this.network.tiles.values()) for (const lane of tile.lanes) {
      const key = `${tile.tile}|${lane.carriageway}`, list = siblings.get(key);
      if (list) list.push(lane); else siblings.set(key, [lane]);
      for (const { lane: next, route } of this.exits(lane, flow)) add(next, lane, route);
    }
    this.incoming = incoming; this.siblings = siblings;
    return this.flow = flow;
  }

  /** Where a car on `lane` can go at its end: junction movements (with their connector) or the seam successor. */
  private exits(lane: Lane, flow: TrafficFlow): { lane: Lane; route: Lane | null }[] {
    const out: { lane: Lane; route: Lane | null }[] = [];
    for (const route of flow.routesOf(lane)) if (route.next && route.connector && route !== lane) out.push({ lane: route.next, route });
    if (!out.length) {
      const next = this.network.successor(lane);
      if (next) out.push({ lane: next, route: null });
    }
    return out;
  }

  lane(ref: LaneRef): Lane | null { return this.network.tiles.get(ref.tile)?.byId.get(ref.id) ?? null; }
  /** Every held lane (drive tests enumerate them). */
  lanes(): Lane[] { this.graph(); return [...this.network.tiles.values()].flatMap(t => t.lanes); }

  /** Lanes within `maxDistance` of a point (same level when both heights are known), nearest first. */
  near(p: RoutePoint, maxDistance = 40, count = 6): { lane: Lane; s: number; distance: number }[] {
    this.graph();
    const out: { lane: Lane; s: number; distance: number }[] = [];
    for (const tile of this.network.tiles.values()) for (const lane of tile.lanes) {
      const b = lane.bbox;
      if (p.x < b[0] - maxDistance || p.x > b[2] + maxDistance || p.z < b[1] - maxDistance || p.z > b[3] + maxDistance) continue;
      const at = project(lanePath(lane), p.x, p.z, 0, Infinity, p.y, 3);
      if (at.distance <= maxDistance) out.push({ lane, s: at.s, distance: at.distance });
    }
    return out.sort((a, b) => a.distance - b.distance).slice(0, count);
  }

  /** The lane nearest a point: same level (lane Y within 3 m when both are known) and, when a heading is given,
   *  pointing the same way (a car on the right-hand lane is never snapped onto the oncoming one). */
  snap(p: RoutePoint, maxDistance = 25): { lane: Lane; s: number; distance: number } | null {
    this.graph();
    let best: { lane: Lane; s: number; distance: number; score: number } | null = null;
    for (const tile of this.network.tiles.values()) for (const lane of tile.lanes) {
      const b = lane.bbox;
      if (p.x < b[0] - maxDistance || p.x > b[2] + maxDistance || p.z < b[1] - maxDistance || p.z > b[3] + maxDistance) continue;
      const path = lanePath(lane);
      const at = project(path, p.x, p.z, 0, Infinity, p.y, 3);
      if (at.distance > maxDistance) continue;
      let score = at.distance;
      if (p.hx !== undefined && p.hz !== undefined) {
        const i = Math.min(lane.cum.length - 2, Math.max(0, lane.cum.findIndex(c => c >= at.s) - 1));
        const dx = lane.points[2 * i + 2] - lane.points[2 * i], dz = lane.points[2 * i + 3] - lane.points[2 * i + 1];
        const cos = (dx * p.hx + dz * p.hz) / (Math.hypot(dx, dz) || 1);
        if (cos < 0.2) continue;
        score += (1 - cos) * 6;
      }
      if (!best || score < best.score) best = { lane, s: at.s, distance: at.distance, score };
    }
    return best ? { lane: best.lane, s: best.s, distance: best.distance } : null;
  }

  /** Shortest lane route from a pose to a point, or null when the held lanes do not connect them. Every lane near
   *  the start is a candidate source (one pointing against the car's heading or farther away costs extra) and every
   *  lane near the target a candidate goal, so a car in a yard or a target across the street still routes. */
  route(from: RoutePoint, to: RoutePoint): LaneRoute | null {
    const starts = this.near({ x: from.x, z: from.z, y: from.y }, 40, 8).map(c => {
      let penalty = 2 * c.distance;
      if (from.hx !== undefined && from.hz !== undefined) {
        const i = Math.min(c.lane.cum.length - 2, Math.max(0, c.lane.cum.findIndex(v => v >= c.s) - 1));
        const dx = c.lane.points[2 * i + 2] - c.lane.points[2 * i], dz = c.lane.points[2 * i + 3] - c.lane.points[2 * i + 1];
        const cos = (dx * from.hx + dz * from.hz) / (Math.hypot(dx, dz) || 1);
        penalty += (1 - cos) * 15 + (cos < 0.2 ? 30 : 0);
      }
      return { ...c, penalty };
    });
    const goals = new Map(this.near({ x: to.x, z: to.z, y: to.y }).map(g => [g.lane, { s: g.s, penalty: 2 * g.distance }]));
    if (!starts.length || !goals.size) return null;
    const flow = this.graph();
    // g(lane) is the cost at the lane's start; a start lane begins s metres in.
    const g = new Map<Lane, number>(), prev = new Map<Lane, Step>(), done = new Set<Lane>(), entry = new Map<Lane, number>();
    const heap = new MinHeap<Lane>();
    const h = (lane: Lane) => Math.hypot(lane.points[0] - to.x, lane.points[1] - to.z);
    let goalCost = Infinity, goalStep: Step | null = null, goalS = 0;
    for (const start of starts) {
      const cost = start.penalty - start.s;
      if (cost >= (g.get(start.lane) ?? Infinity)) continue;
      const first: Step = { lane: start.lane, via: 'start', route: null, from: null };
      g.set(start.lane, cost); prev.set(start.lane, first); entry.set(start.lane, start.s);
      heap.push(start.lane, cost + h(start.lane));
      const own = goals.get(start.lane);
      if (own && own.s >= start.s && cost + own.s + own.penalty < goalCost) { goalCost = cost + own.s + own.penalty; goalStep = first; goalS = own.s; }
    }
    const reach = (lane: Lane, cost: number, step: Step) => {
      const goal = goals.get(lane);
      if (goal && cost + goal.s + goal.penalty < goalCost) { goalCost = cost + goal.s + goal.penalty; goalStep = step; goalS = goal.s; }
      if (cost >= (g.get(lane) ?? Infinity)) return;
      g.set(lane, cost); prev.set(lane, step); entry.delete(lane); heap.push(lane, cost + h(lane));
    };
    while (heap.size) {
      const [lane, key] = heap.pop();
      if (key >= goalCost) break;
      if (done.has(lane)) continue;
      done.add(lane);
      const at = g.get(lane)!;
      for (const { lane: next, route } of this.exits(lane, flow)) {
        reach(next, at + lane.length + (route?.connector?.length ?? 0), { lane: next, via: route ? 'move' : 'seam', route, from: lane });
      }
      for (const sib of this.siblings!.get(`${lane.tile}|${lane.carriageway}`) ?? []) {
        if (Math.abs(sib.k - lane.k) !== 1) continue;
        // A change on a start lane needs room ahead of the car.
        const s0 = entry.get(lane);
        if (s0 !== undefined && goals.has(sib) && goals.get(sib)!.s < s0 + CHANGE_LENGTH) continue;
        reach(sib, at + CHANGE_COST, { lane: sib, via: 'change', route: null, from: lane });
      }
    }
    if (!goalStep) return null;
    const steps: Step[] = [];
    for (let step: Step | undefined = goalStep, guard = 0; step && guard < 100000; guard++) {
      steps.push(step);
      if (step.via === 'start') break;
      step = prev.get(step.from!);
    }
    steps.reverse();
    if (steps[0]?.via !== 'start') return null;
    const s0 = starts.find(c => c.lane === steps[0].lane)!.s;
    return assemble(steps, s0, goalS);
  }

  /** A path along exactly this lane, extended along its straightest predecessors and successors by `before` and
   *  `after` metres so a car enters it at speed and leaves it settled. `laneStart`/`laneEnd` are the lane's own
   *  arc range on the path. Null for an unknown lane. */
  follow(ref: LaneRef, before = 60, after = 40): (LaneRoute & { laneStart: number; laneEnd: number }) | null {
    const lane = this.lane(ref);
    if (!lane) return null;
    const flow = this.graph();
    const turnOf = (route: Lane | null) => Math.abs(route?.connector?.turn ?? 0);
    const used = new Set<Lane>([lane]);
    const back: Step[] = [];
    let need = before, at = lane, s0 = 0;
    for (let guard = 0; need > 0 && guard < 50; guard++) {
      const options = (this.incoming!.get(at) ?? []).filter(o => !used.has(o.from));
      if (!options.length) break;
      const pick = options.sort((a, b) => turnOf(a.route) - turnOf(b.route) || (a.from.id < b.from.id ? -1 : 1))[0];
      back.push({ lane: at, via: pick.route ? 'move' : 'seam', route: pick.route, from: pick.from });
      need -= pick.route?.connector?.length ?? 0;
      at = pick.from; used.add(at);
      if (at.length >= need) { s0 = Math.max(0, at.length - Math.max(0, need)); need = 0; } else need -= at.length;
    }
    const steps: Step[] = [{ lane: at, via: 'start', route: null, from: null }, ...back.reverse()];
    let forward = after, tail = lane, s1 = lane.length;
    for (let guard = 0; forward > 0 && guard < 50; guard++) {
      const options = this.exits(tail, flow).filter(o => !used.has(o.lane));
      if (!options.length) break;
      const pick = options.sort((a, b) => turnOf(a.route) - turnOf(b.route) || (a.lane.id < b.lane.id ? -1 : 1))[0];
      forward -= pick.route?.connector?.length ?? 0;
      steps.push({ lane: pick.lane, via: pick.route ? 'move' : 'seam', route: pick.route, from: tail });
      tail = pick.lane; used.add(tail);
      if (tail.length >= forward) { s1 = Math.max(0.5, forward); forward = 0; } else { forward -= tail.length; s1 = tail.length; }
    }
    const route = assemble(steps, s0, s1);
    const ix = steps.findIndex(s => s.lane === lane);
    const laneStart = route.offsets[ix] ?? 0;
    return { ...route, laneStart, laneEnd: laneStart + lane.length - (ix === 0 ? s0 : 0) };
  }
}

/** One lane as a Path (cached per lane object). */
const lanePaths = new WeakMap<Lane, Path>();
export function lanePath(lane: Lane): Path {
  let path = lanePaths.get(lane);
  if (!path) {
    path = { points: lane.points, ys: lane.ys, cum: lane.cum, length: lane.length };
    lanePaths.set(lane, path);
  }
  return path;
}

function laneSlice(lane: Lane, s0: number, s1: number, out: { x: number; z: number; y?: number }[]) {
  const p = lane.points, c = lane.cum, n = c.length;
  const at = (s: number) => {
    let i = 1;
    while (i < n - 1 && c[i] < s) i++;
    const span = c[i] - c[i - 1] || 1, f = Math.min(1, Math.max(0, (s - c[i - 1]) / span));
    out.push({ x: p[2 * i - 2] + (p[2 * i] - p[2 * i - 2]) * f, z: p[2 * i - 1] + (p[2 * i + 1] - p[2 * i - 1]) * f,
      ...(lane.ys ? { y: lane.ys[i - 1] + (lane.ys[i] - lane.ys[i - 1]) * f } : {}) });
  };
  at(s0);
  for (let i = 1; i < n - 1; i++) if (c[i] > s0 + 1e-3 && c[i] < s1 - 1e-3) out.push({ x: p[2 * i], z: p[2 * i + 1], ...(lane.ys ? { y: lane.ys[i] } : {}) });
  at(s1);
}

/** Steps → one path. Movements add their connector; a lane change crosses to the sibling over CHANGE_LENGTH. */
function assemble(steps: Step[], s0: number, s1: number): LaneRoute & { offsets: number[] } {
  const points: PathPoint[] = [];
  const limits: { from: number; v: number }[] = [];
  const offsets: number[] = [];
  const length = () => {
    let total = 0;
    for (let i = 1; i < points.length; i++) total += Math.hypot(points[i].x - points[i - 1].x, points[i].z - points[i - 1].z);
    return total;
  };
  let entry = s0;
  for (let i = 0; i < steps.length; i++) {
    const { lane } = steps[i], next = steps[i + 1], last = i === steps.length - 1;
    offsets.push(length());
    limits.push({ from: offsets[i], v: lane.speed / LIMIT_SHARE });
    let exit = last ? Math.max(entry, s1) : lane.length;
    if (next?.via === 'change') {
      // Change after a short settle on this lane, within its remaining length.
      const change = Math.min(lane.length - entry, CHANGE_LENGTH);
      exit = Math.min(lane.length, entry + Math.min(6, Math.max(0, lane.length - entry - change)));
      laneSlice(lane, entry, exit, points);
      const target = lanePath(next.lane), from = points.at(-1)!;
      const landing = Math.min(next.lane.length, project(target, from.x, from.z).s + change);
      entry = landing;
      continue;
    }
    laneSlice(lane, entry, exit, points);
    if (next?.route?.connector) {
      const c = next.route.connector.points, y0 = points.at(-1)?.y, y1 = next.lane.ys?.[0];
      const count = c.length / 2;
      for (let k = 1; k < count - 1; k++) {
        const f = k / (count - 1);
        points.push({ x: c[2 * k], z: c[2 * k + 1], ...(y0 !== undefined && y1 !== undefined ? { y: y0 + (y1 - y0) * f } : {}) });
      }
    }
    entry = 0;
  }
  const path = makePath(points);
  const lanes = steps.map(s => ({ tile: s.lane.tile, id: s.lane.id }));
  return { path, lanes, offsets, limit: (s: number) => {
    let v = limits[0]?.v ?? 14;
    for (const l of limits) { if (l.from <= s) v = l.v; else break; }
    return v;
  } };
}

class MinHeap<T> {
  private items: T[] = [];
  private keys: number[] = [];
  get size() { return this.items.length; }
  push(item: T, key: number) {
    let i = this.items.length;
    this.items.push(item); this.keys.push(key);
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (this.keys[p] <= key) break;
      this.items[i] = this.items[p]; this.keys[i] = this.keys[p];
      i = p;
    }
    this.items[i] = item; this.keys[i] = key;
  }
  pop(): [T, number] {
    const top = this.items[0], topKey = this.keys[0];
    const item = this.items.pop()!, key = this.keys.pop()!;
    if (this.items.length) {
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1;
        let m = i, mk = key;
        if (l < this.items.length && this.keys[l] < mk) { m = l; mk = this.keys[l]; }
        if (r < this.items.length && this.keys[r] < mk) m = r;
        if (m === i) break;
        this.items[i] = this.items[m]; this.keys[i] = this.keys[m];
        i = m;
      }
      this.items[i] = item; this.keys[i] = key;
    }
    return [top, topKey];
  }
}

export { tileOf as laneTileOf };
