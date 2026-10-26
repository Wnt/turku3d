// Shortest street route (contract Router): edge-projection snap plus A* over the road graph.
import type { Router, WorldPoint } from './contract';
import { CAR, ONEWAY, edgeLength, modeBit, type RoadEdge, type RoadGraph, type TravelMode } from './roadGraph';

/** A point on an edge: `offset` metres from node a along the polyline. */
interface Snap { edge: number; offset: number; point: WorldPoint; distance: number }

const CELL = 32;
/** Farther than this from every usable street counts as off the graph. */
export const MAX_SNAP_M = 80;

export class StreetRouter implements Router {
  /** Travel mode for route(): main.ts keeps it equal to the player's current mode. */
  mode: TravelMode = 'foot';
  private readonly grid = new Map<string, Array<[edge: number, segment: number]>>();

  constructor(readonly graph: RoadGraph) {
    graph.edges.forEach((edge, e) => {
      for (let i = 0; i + 1 < edge.cumulative.length; i++) {
        const [x0, z0, x1, z1] = edge.points.subarray(2 * i, 2 * i + 4);
        for (let cx = Math.floor(Math.min(x0, x1) / CELL); cx <= Math.floor(Math.max(x0, x1) / CELL); cx++) {
          for (let cz = Math.floor(Math.min(z0, z1) / CELL); cz <= Math.floor(Math.max(z0, z1) / CELL); cz++) {
            const key = `${cx},${cz}`;
            const list = this.grid.get(key) ?? [];
            if (!list.length) this.grid.set(key, list);
            list.push([e, i]);
          }
        }
      }
    });
  }

  /** Nearest point on any edge usable in `mode`, or null beyond MAX_SNAP_M. */
  snap(p: WorldPoint, mode: TravelMode = this.mode): Snap | null {
    const bit = modeBit(mode);
    let best: Snap | null = null;
    const r = Math.ceil(MAX_SNAP_M / CELL);
    const cx = Math.floor(p.x / CELL), cz = Math.floor(p.z / CELL);
    for (let i = cx - r; i <= cx + r; i++) {
      for (let j = cz - r; j <= cz + r; j++) {
        for (const [e, s] of this.grid.get(`${i},${j}`) ?? []) {
          const edge = this.graph.edges[e];
          if (!(edge.flags & bit)) continue;
          const [x0, z0, x1, z1] = edge.points.subarray(2 * s, 2 * s + 4);
          const dx = x1 - x0, dz = z1 - z0, len2 = dx * dx + dz * dz;
          const t = len2 > 0 ? Math.min(1, Math.max(0, ((p.x - x0) * dx + (p.z - z0) * dz) / len2)) : 0;
          const x = x0 + t * dx, z = z0 + t * dz, distance = Math.hypot(p.x - x, p.z - z);
          if (distance <= MAX_SNAP_M && (!best || distance < best.distance)) {
            best = { edge: e, offset: edge.cumulative[s] + t * Math.sqrt(len2), point: { x, z }, distance };
          }
        }
      }
    }
    return best;
  }

  route(from: WorldPoint, to: WorldPoint): WorldPoint[] | null {
    const start = this.snap(from), goal = this.snap(to);
    if (!start || !goal) return null;
    const { edges, adjacency, nodes } = this.graph;
    const bit = modeBit(this.mode);
    const forwardOnly = (edge: RoadEdge) => bit === CAR && !!(edge.flags & ONEWAY);
    const n = nodes.length / 2, S = n, T = n + 1;
    const g = new Float64Array(n + 2).fill(Infinity);
    // How each vertex was reached: previous vertex, edge walked and the offsets walked on it.
    const prev = new Int32Array(n + 2).fill(-1), via = new Int32Array(n + 2).fill(-1);
    const fromOffset = new Float64Array(n + 2), toOffset = new Float64Array(n + 2);
    const done = new Uint8Array(n + 2);
    const gx = goal.point.x, gz = goal.point.z;
    const h = (v: number) => v === T ? 0 : v === S
      ? Math.hypot(start.point.x - gx, start.point.z - gz)
      : Math.hypot(nodes[2 * v] - gx, nodes[2 * v + 1] - gz);
    const heap = new MinHeap();
    const relax = (u: number, v: number, edge: number, from: number, to: number) => {
      const cost = g[u] + Math.abs(to - from);
      if (cost >= g[v]) return;
      g[v] = cost; prev[v] = u; via[v] = edge; fromOffset[v] = from; toOffset[v] = to;
      heap.push(v, cost + h(v));
    };
    const startEdge = edges[start.edge], goalEdge = edges[goal.edge];
    g[S] = 0;
    heap.push(S, h(S));
    while (heap.size) {
      const u = heap.pop();
      if (done[u]) continue;
      done[u] = 1;
      if (u === T) break;
      if (u === S) {
        relax(S, startEdge.b, start.edge, start.offset, edgeLength(startEdge));
        if (!forwardOnly(startEdge)) relax(S, startEdge.a, start.edge, start.offset, 0);
        if (start.edge === goal.edge && (goal.offset >= start.offset || !forwardOnly(startEdge))) {
          relax(S, T, start.edge, start.offset, goal.offset);
        }
        continue;
      }
      if (goalEdge.a === u) relax(u, T, goal.edge, 0, goal.offset);
      if (goalEdge.b === u && !forwardOnly(goalEdge)) relax(u, T, goal.edge, edgeLength(goalEdge), goal.offset);
      for (const e of adjacency[u]) {
        const edge = edges[e];
        if (!(edge.flags & bit)) continue;
        if (edge.a === u) relax(u, edge.b, e, 0, edgeLength(edge));
        if (edge.b === u && !forwardOnly(edge)) relax(u, edge.a, e, edgeLength(edge), 0);
      }
    }
    if (!done[T]) return null;
    // Walk back from T, emitting each step's geometry in reverse.
    const reversed: WorldPoint[] = [goal.point];
    for (let v = T; v !== S; v = prev[v]) {
      const u = prev[v];
      reversed.push(...this.slice(via[v], toOffset[v], fromOffset[v]));
      reversed.push(u === S ? start.point : { x: nodes[2 * u], z: nodes[2 * u + 1] });
    }
    return dedupe(reversed.reverse());
  }

  distance(path: WorldPoint[]): number {
    let total = 0;
    for (let i = 1; i < path.length; i++) total += Math.hypot(path[i].x - path[i - 1].x, path[i].z - path[i - 1].z);
    return total;
  }

  /** Interior polyline vertices strictly between offsets `from` and `to`, in walking order. */
  private slice(e: number, from: number, to: number): WorldPoint[] {
    const edge = this.graph.edges[e];
    const out: WorldPoint[] = [];
    const c = edge.cumulative;
    if (from <= to) {
      for (let i = 0; i < c.length; i++) if (c[i] > from && c[i] < to) out.push({ x: edge.points[2 * i], z: edge.points[2 * i + 1] });
    } else {
      for (let i = c.length - 1; i >= 0; i--) if (c[i] < from && c[i] > to) out.push({ x: edge.points[2 * i], z: edge.points[2 * i + 1] });
    }
    return out;
  }
}

function dedupe(points: WorldPoint[]): WorldPoint[] {
  return points.filter((p, i) => i === 0 || Math.hypot(p.x - points[i - 1].x, p.z - points[i - 1].z) > 1e-6);
}

class MinHeap {
  private items: number[] = [];
  private keys: number[] = [];
  get size() { return this.items.length; }
  push(item: number, key: number) {
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
  pop(): number {
    const top = this.items[0];
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
    return top;
  }
}
