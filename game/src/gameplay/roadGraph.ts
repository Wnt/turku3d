// Street graph published by pipeline/turku3d/roads (format turku3d-roadgraph v1; revision 2 adds stable ids,
// lanes, heights, junctions, signals and crossings: docs/specs/roads-json-v2.md).
import type { Manifest } from '../world/manifest';

export const FOOT = 1, CAR = 2, ONEWAY = 4;
const CLASS_SHIFT = 3;
export type TravelMode = 'foot' | 'car';
export const modeBit = (mode: TravelMode) => (mode === 'car' ? CAR : FOOT);

/** roads.json v2 per-edge flags (spec §3.5). */
export const EDGE = {
  PAVED: 1, LIT: 2, BRIDGE: 4, TUNNEL: 8, ROUNDABOUT: 16, LINK: 32, KMH_TAGGED: 64, LANES_TAGGED: 128,
  WIDTH_TAGGED: 256, START_JUNCTION: 512, END_JUNCTION: 1024, SHARED_SPACE: 2048, PSV_ONLY: 4096,
  NO_MARKINGS: 8192, SINGLE_TRACK: 16384, SMOOTH: 32768,
} as const;
/** roads.json v2 node flags (spec §3.6). */
export const NODE = { JUNCTION: 1, SIGNALS: 2, CROSSING: 4, GIVE_WAY: 8, STOP: 16, SEAM: 32, DEAD_END: 64 } as const;
/** roads.json v2 event kinds (low 4 bits of `code`) and bits (spec §3.7). */
export const EVENT = {
  SIGNAL: 1, CROSSING: 2, GIVE_WAY: 3, STOP: 4,
  FORWARD: 16, BACKWARD: 32, MARKED: 64, SIGNALISED: 128, EXPLICIT: 256, CYCLE: 512,
} as const;

export interface RoadLanes {
  /** Car lanes in a→b and b→a direction (0, 0 on foot-only edges). */
  fwd: number;
  bwd: number;
  /** Carriageway width (m), kerb to kerb. */
  width: number;
}

export interface RoadEvent {
  /** Arc length (m) from node a. */
  s: number;
  /** kind | direction | detail bits (`EVENT`). */
  code: number;
  /** Index into `RoadGraph.controllers`, or -1. */
  controller: number;
}

export interface RoadEdge {
  a: number;
  b: number;
  flags: number;
  /** x,z pairs from node a to node b, both ends included. */
  points: Float64Array;
  /** Cumulative polyline length at each point; the last value is the edge length. */
  cumulative: Float64Array;
  /** Surface Y per vertex: every v2 edge; v1 only on draft bridge decks and tunnels. */
  heights?: Float64Array;
  /** Stable, seam-independent id of the logical edge (spec §3.2); v1 rows get a "v1:" fallback. */
  id: string;
  /** [s0, s1, length]: this row's arc range on its logical edge. */
  span: [number, number, number];
  lanes: RoadLanes;
  kmh: number;
  carDensity: number;
  footDensity: number;
  /** v2 edge flags (`EDGE`). */
  eflags: number;
  /** OSM way id, 0 when unknown. */
  way: number;
  highway: string;
  /** Junction-box distance from the logical start and end (m). */
  setbacks: [number, number];
  events: RoadEvent[];
}

export interface RoadController { id: string; x: number; z: number }

export interface RoadGraph {
  /** x,z pairs per node. */
  nodes: Float64Array;
  edges: RoadEdge[];
  /** Edge indices touching each node. */
  adjacency: number[][];
  bbox: [number, number, number, number];
  /** v2 node flags (`NODE`) per node; 0 for v1 nodes. */
  nodeFlags: Uint16Array;
  /** Signal groups referenced by `RoadEvent.controller`. */
  controllers: RoadController[];
}

export const edgeLength = (edge: RoadEdge) => edge.cumulative[edge.cumulative.length - 1];
/** Road class 0 path, 1 service, 2 street, 3 major. */
export const edgeClass = (edge: RoadEdge) => (edge.flags >> CLASS_SHIFT) & 3;
/** 0…5 for ≤20, 30, 40, 50, 60, ≥70 km/h. */
export const speedTier = (edge: RoadEdge) => {
  const k = edge.kmh;
  return k <= 20 ? 0 : k <= 30 ? 1 : k <= 40 ? 2 : k <= 50 ? 3 : k <= 60 ? 4 : 5;
};

export interface RoadGraphJson {
  format: string; version: number; frame: string;
  revision?: number;
  bbox: [number, number, number, number];
  bits?: { highways?: string[] } & Record<string, unknown>;
  nodes: number[];
  edges: number[][];
  edgeHeights?: (number[] | null)[];
  edgeIds?: string[];
  edgeAttrColumns?: string[];
  edgeAttrs?: number[][];
  edgeEvents?: (number[] | null)[];
  nodeFlags?: number[];
  controllers?: [string, number, number][];
}

function checkFormat(json: RoadGraphJson) {
  if (json.format !== 'turku3d-roadgraph' || json.version !== 1 || json.frame !== 'engine-xz') {
    throw new Error(`road graph: unsupported ${json.format} v${json.version} (${json.frame})`);
  }
}

/** cyrb53: a small deterministic 53-bit string hash (v1 fallback ids only). */
function hash53(text: string) {
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 2654435761); h2 = Math.imul(h2 ^ c, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(16).padStart(14, '0');
}

const quant = (v: number) => Math.floor(v / 0.5 + 0.5);

function pointAt(points: ArrayLike<number>, cumulative: ArrayLike<number>, s: number): [number, number] {
  const n = cumulative.length;
  for (let i = 1; i < n; i++) {
    if (cumulative[i] >= s || i === n - 1) {
      const span = cumulative[i] - cumulative[i - 1];
      const t = span > 0 ? Math.min(Math.max((s - cumulative[i - 1]) / span, 0), 1) : 0;
      return [points[2 * i - 2] + t * (points[2 * i] - points[2 * i - 2]), points[2 * i - 1] + t * (points[2 * i + 1] - points[2 * i - 1])];
    }
  }
  return [points[0], points[1]];
}

/** Spec §4: "v1:" + hash of the quantised start, end and arc midpoint. */
function fallbackId(points: Float64Array, cumulative: Float64Array) {
  const n = points.length;
  const [mx, mz] = pointAt(points, cumulative, cumulative[cumulative.length - 1] / 2);
  return 'v1:' + hash53([points[0], points[1], points[n - 2], points[n - 1], mx, mz].map(quant).join(','));
}

const V1_WIDTH = [2.5, 5, 6.5, 7];

interface Row {
  points: Float64Array;
  cumulative: Float64Array;
  heights: Float64Array | null;
  flags: number;
  id: string;
  span: [number, number, number];
  lanes: RoadLanes;
  kmh: number; carDensity: number; footDensity: number; eflags: number; way: number; highway: string;
  setbacks: [number, number];
  /** Events with controller ids (resolved to indices when the graph is assembled). */
  events: { s: number; code: number; controller: string | null }[];
  v2: boolean;
}

function decode(json: RoadGraphJson): { rows: Row[]; nodeFlags: Map<number, number>; controllers: Map<string, RoadController> } {
  checkFormat(json);
  const nodes = json.nodes;
  const count = nodes.length / 2;
  const v2 = (json.revision ?? 1) >= 2 && !!json.edgeIds && !!json.edgeAttrs && !!json.edgeAttrColumns;
  const col = new Map((json.edgeAttrColumns ?? []).map((name, i) => [name, i]));
  const highways = (json.bits?.highways as string[] | undefined) ?? [];
  const controllers = new Map<string, RoadController>();
  const controllerIds = (json.controllers ?? []).map(([id, x, z]) => { controllers.set(id, { id, x, z }); return id; });
  const rows = json.edges.map((row, index): Row => {
    const [a, b, flags] = row;
    if (!(a >= 0 && a < count && b >= 0 && b < count) || row.length % 2) throw new Error(`road graph: bad edge ${index}`);
    const points = new Float64Array(row.length);
    points.set([nodes[2 * a], nodes[2 * a + 1]], 0);
    points.set(row.slice(4), 2);
    points.set([nodes[2 * b], nodes[2 * b + 1]], row.length - 2);
    const cumulative = new Float64Array(points.length / 2);
    for (let i = 1; i < cumulative.length; i++) {
      cumulative[i] = cumulative[i - 1] + Math.hypot(points[2 * i] - points[2 * i - 2], points[2 * i + 1] - points[2 * i - 1]);
    }
    const ys = json.edgeHeights?.[index];
    if (ys && (ys.length !== cumulative.length || !ys.every(Number.isFinite))) throw new Error(`road graph: bad heights ${index}`);
    const length = cumulative[cumulative.length - 1];
    const base = { points, cumulative, heights: ys ? Float64Array.from(ys) : null, flags };
    if (v2) {
      const attrs = json.edgeAttrs![index];
      const get = (name: string, fallback = 0) => { const i = col.get(name); return i === undefined ? fallback : attrs[i]; };
      const flat = json.edgeEvents?.[index] ?? [];
      const events = [];
      for (let i = 0; i + 2 < flat.length; i += 3) {
        events.push({ s: flat[i], code: flat[i + 1], controller: flat[i + 2] >= 0 ? controllerIds[flat[i + 2]] ?? null : null });
      }
      return {
        ...base, id: json.edgeIds![index], span: [get('s0'), get('s1', length), get('length', length)],
        lanes: { fwd: get('lanes_fwd'), bwd: get('lanes_bwd'), width: get('width', 3) },
        kmh: get('kmh', 30), carDensity: get('car_density'), footDensity: get('foot_density'), eflags: get('flags'),
        way: get('way'), highway: highways[get('highway', -1)] ?? 'other', setbacks: [get('setback0'), get('setback1')],
        events, v2: true,
      };
    }
    const car = (flags & CAR) !== 0, cls = (flags >> CLASS_SHIFT) & 3;
    return {
      ...base, id: fallbackId(points, cumulative), span: [0, length, length],
      lanes: { fwd: car ? 1 : 0, bwd: car && !(flags & ONEWAY) ? 1 : 0, width: car ? V1_WIDTH[cls] : 2.5 },
      kmh: car ? [10, 20, 40, 50][cls] : 10, carDensity: car ? [0, 2, 4, 11][cls] : 0, footDensity: flags & FOOT ? 4 : 0,
      eflags: EDGE.PAVED, way: 0, highway: 'other', setbacks: [0, 0], events: [], v2: false,
    };
  });
  const nodeFlags = new Map<number, number>();
  json.nodeFlags?.forEach((f, i) => { if (f) nodeFlags.set(i, f); });
  return { rows, nodeFlags, controllers };
}

function assemble(nodes: Float64Array, rows: Row[], ends: [number, number][], nodeFlags: Uint16Array,
  controllers: RoadController[], bbox: [number, number, number, number]): RoadGraph {
  const controllerIndex = new Map(controllers.map((c, i) => [c.id, i]));
  const adjacency: number[][] = Array.from({ length: nodes.length / 2 }, () => []);
  const edges = rows.map((row, index): RoadEdge => {
    const [a, b] = ends[index];
    adjacency[a].push(index);
    if (b !== a) adjacency[b].push(index);
    const { points, cumulative, flags, id, span, lanes, kmh, carDensity, footDensity, eflags, way, highway, setbacks } = row;
    const events = row.events.map(e => ({ s: e.s, code: e.code, controller: e.controller === null ? -1 : controllerIndex.get(e.controller) ?? -1 }));
    return { a, b, flags, points, cumulative, ...(row.heights ? { heights: row.heights } : {}), id, span, lanes, kmh, carDensity,
      footDensity, eflags, way, highway, setbacks, events };
  });
  return { nodes, edges, adjacency, bbox, nodeFlags, controllers };
}

export function parseRoadGraph(json: RoadGraphJson): RoadGraph {
  const { rows, nodeFlags, controllers } = decode(json);
  const flags = new Uint16Array(json.nodes.length / 2);
  for (const [i, f] of nodeFlags) flags[i] = f;
  const ends = json.edges.map(row => [row[0], row[1]] as [number, number]);
  return assemble(Float64Array.from(json.nodes), rows, ends, flags, [...controllers.values()], json.bbox);
}

export const EMPTY_ROAD_GRAPH: RoadGraph = {
  nodes: new Float64Array(), edges: [], adjacency: [], bbox: [0, 0, 0, 0], nodeFlags: new Uint16Array(), controllers: [],
};

/** Endpoint clusters: points within 0.11 m are one node (a seam point can straddle two 0.1 m rounding bins after
 *  independent tile clipping). The representative is the cluster's smallest (x, z), so the result does not
 *  depend on input order. */
class NodeClusters {
  private xs: number[] = []; private zs: number[] = []; private parent: number[] = [];
  private cells = new Map<string, number[]>();
  add(x: number, z: number) {
    const cx = Math.floor(x * 10), cz = Math.floor(z * 10), k = this.xs.length;
    this.xs.push(x); this.zs.push(z); this.parent.push(k);
    for (let dx = -2; dx <= 2; dx++) for (let dz = -2; dz <= 2; dz++) {
      for (const j of this.cells.get(`${cx + dx},${cz + dz}`) ?? []) {
        if (Math.hypot(this.xs[j] - x, this.zs[j] - z) <= 0.11) this.union(j, k);
      }
    }
    const key = `${cx},${cz}`, cell = this.cells.get(key);
    if (cell) cell.push(k); else this.cells.set(key, [k]);
    return k;
  }
  root(k: number): number {
    while (this.parent[k] !== k) { this.parent[k] = this.parent[this.parent[k]]; k = this.parent[k]; }
    return k;
  }
  private union(a: number, b: number) {
    a = this.root(a); b = this.root(b);
    if (a === b) return;
    const first = this.xs[a] < this.xs[b] || (this.xs[a] === this.xs[b] && this.zs[a] <= this.zs[b]);
    if (first) this.parent[b] = a; else this.parent[a] = b;
  }
  x(k: number) { return this.xs[this.root(k)]; }
  z(k: number) { return this.zs[this.root(k)]; }
}

const compareRows = (p: Row, q: Row) => (p.id < q.id ? -1 : p.id > q.id ? 1 : p.span[0] - q.span[0]);

/** Pieces of one logical edge that meet at a shared node continue as one edge (spec §4). */
function join(p: Row, q: Row): Row {
  const n = p.cumulative.length, length = p.cumulative[n - 1];
  const points = new Float64Array(p.points.length + q.points.length - 2);
  points.set(p.points); points.set(q.points.subarray(2), p.points.length);
  const cumulative = new Float64Array(n + q.cumulative.length - 1);
  cumulative.set(p.cumulative);
  for (let i = 1; i < q.cumulative.length; i++) cumulative[n + i - 1] = length + q.cumulative[i];
  const heights = p.heights && q.heights ? Float64Array.from([...p.heights, ...q.heights.subarray(1)]) : null;
  const events = [...p.events];
  for (const e of q.events) {
    const s = e.s + length;
    if (!events.some(o => o.code === e.code && Math.abs(o.s - s) < 0.1)) events.push({ ...e, s });
  }
  return { ...p, points, cumulative, heights, span: [p.span[0], q.span[1], p.span[2]], events };
}

/** One graph from several: the published one and the draft tiles' (pipeline/turku3d/coarse roads.json). Each part is
 *  clipped to its own area, so a street leaving one part ends on its edge at the same point (to 0.1 m, the pipeline's
 *  node precision) where it enters the next: nodes merge by coordinate and the parts join there. An edge both parts
 *  carry (overlapping areas) is kept once, a v2 copy over a v1 copy. Pieces of one v2 logical edge re-join across
 *  seams. The result is canonical (spec §4): the same parts in any order give identical node and edge indices,
 *  ids, spans, lanes, heights and events. */
export function mergeRoadGraphs(parts: readonly RoadGraphJson[]): RoadGraph {
  if (!parts.length) return EMPTY_ROAD_GRAPH;
  if (parts.length === 1) return parseRoadGraph(parts[0]);
  const clusters = new NodeClusters();
  const bbox: [number, number, number, number] = [Infinity, Infinity, -Infinity, -Infinity];
  const controllers = new Map<string, RoadController>();
  const flagged: [number, number][] = [];
  let rows: { row: Row; a: number; b: number }[] = [];
  for (const part of parts) {
    const decoded = decode(part);
    const map = Array.from({ length: part.nodes.length / 2 }, (_, i) => clusters.add(part.nodes[2 * i], part.nodes[2 * i + 1]));
    for (const [i, f] of decoded.nodeFlags) flagged.push([map[i], f]);
    decoded.rows.forEach((row, i) => rows.push({ row, a: map[part.edges[i][0]], b: map[part.edges[i][1]] }));
    for (const c of decoded.controllers.values()) if (!controllers.has(c.id)) controllers.set(c.id, c);
    for (let k = 0; k < 4; k++) bbox[k] = k < 2 ? Math.min(bbox[k], part.bbox[k]) : Math.max(bbox[k], part.bbox[k]);
  }
  const key = (k: number) => `${clusters.x(k)},${clusters.z(k)}`;
  // Duplicates: v2 by id and span, v1 by geometry; a v1 row whose ends and length a v2 row matches is dropped.
  const seen = new Set<string>(), v2Pairs = new Map<string, number[]>();
  const pair = (r: { a: number; b: number }) => [key(r.a), key(r.b)].sort().join('|');
  for (const r of rows) if (r.row.v2) v2Pairs.set(pair(r), [...v2Pairs.get(pair(r)) ?? [], r.row.cumulative[r.row.cumulative.length - 1]]);
  rows = rows.filter(r => {
    const length = r.row.cumulative[r.row.cumulative.length - 1];
    const k = r.row.v2 ? `${r.row.id}@${Math.round(r.row.span[0] * 10)}`
      : `${key(r.a)}|${key(r.b)}|${r.row.flags}|${Math.round(length * 10)}|${[...r.row.points.subarray(2, -2)].map(v => Math.round(v * 10)).join(',')}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return r.row.v2 || !(v2Pairs.get(pair(r)) ?? []).some(l => Math.abs(l - length) < 1);
  });
  const length = (r: Row) => r.cumulative[r.cumulative.length - 1];
  rows.sort((p, q) => compareRows(p.row, q.row) || key(p.a).localeCompare(key(q.a)) || key(p.b).localeCompare(key(q.b))
    || p.row.flags - q.row.flags || length(p.row) - length(q.row));
  const joined: { row: Row; a: number; b: number }[] = [];
  for (const r of rows) {
    const last = joined[joined.length - 1];
    if (last && last.row.v2 && r.row.v2 && last.row.id === r.row.id && Math.abs(last.row.span[1] - r.row.span[0]) < 0.2
      && clusters.root(last.b) === clusters.root(r.a)) {
      joined[joined.length - 1] = { row: join(last.row, r.row), a: last.a, b: r.b };
    } else {
      joined.push(r);
    }
  }
  // Nodes: the endpoints still in use, ordered by coordinate.
  const used = [...new Set(joined.flatMap(r => [clusters.root(r.a), clusters.root(r.b)]))]
    .sort((p, q) => clusters.x(p) - clusters.x(q) || clusters.z(p) - clusters.z(q));
  const index = new Map(used.map((k, i) => [k, i]));
  const nodes = new Float64Array(used.length * 2);
  used.forEach((k, i) => { nodes[2 * i] = clusters.x(k); nodes[2 * i + 1] = clusters.z(k); });
  const nodeFlags = new Uint16Array(used.length);
  for (const [k, f] of flagged) {
    const i = index.get(clusters.root(k));
    if (i !== undefined) nodeFlags[i] |= f;
  }
  // A seam node that now joins both pieces of its logical edge is no longer a tile edge end.
  const degree = new Uint16Array(used.length);
  for (const r of joined) { degree[index.get(clusters.root(r.a))!]++; degree[index.get(clusters.root(r.b))!]++; }
  for (let i = 0; i < used.length; i++) if (nodeFlags[i] & NODE.SEAM && degree[i] > 1) nodeFlags[i] &= ~NODE.SEAM;
  const ends = joined.map(r => [index.get(clusters.root(r.a))!, index.get(clusters.root(r.b))!] as [number, number]);
  const list = [...controllers.values()].sort((p, q) => (p.id < q.id ? -1 : p.id > q.id ? 1 : 0));
  return assemble(nodes, joined.map(r => r.row), ends, nodeFlags, list, bbox);
}

/** Lane centre offset (m) along the right-hand normal (spec §3.4): `lane` 1…fwd for a→b lanes (1 next to the
 *  divider), -1…-bwd for b→a lanes. */
export function laneOffset(edge: RoadEdge, lane: number) {
  const { fwd, bwd, width } = edge.lanes;
  const w = width / Math.max(fwd + bwd, 1), divider = -width / 2 + bwd * w;
  return lane > 0 ? divider + (lane - 0.5) * w : divider + (lane + 0.5) * w;
}

/** The lane's polyline: the edge offset by `laneOffset` with mitred vertices (mitre limited to 2x), plus Y. */
export function lanePolyline(edge: RoadEdge, lane: number): { points: Float64Array; heights?: Float64Array } {
  const o = laneOffset(edge, lane), p = edge.points, n = p.length / 2;
  const out = new Float64Array(p.length);
  const normal = (i: number) => {
    const dx = p[2 * i + 2] - p[2 * i], dz = p[2 * i + 3] - p[2 * i + 1], l = Math.hypot(dx, dz) || 1;
    return [-dz / l, dx / l];
  };
  for (let i = 0; i < n; i++) {
    const a = normal(Math.max(i - 1, 0)), b = normal(Math.min(i, n - 2));
    let mx = a[0] + b[0], mz = a[1] + b[1];
    const l = Math.hypot(mx, mz) || 1;
    mx /= l; mz /= l;
    const scale = Math.min(1 / Math.max(mx * b[0] + mz * b[1], 1e-6), 2);
    out[2 * i] = p[2 * i] + mx * o * scale;
    out[2 * i + 1] = p[2 * i + 1] + mz * o * scale;
  }
  return { points: out, ...(edge.heights ? { heights: edge.heights } : {}) };
}

/** The published graph's JSON, or null when the tile build carries none (synthetic dev tiles). */
export async function loadRoadGraphJson(manifest: Pick<Manifest, 'roads' | 'baseUrl'>): Promise<RoadGraphJson | null> {
  if (!manifest.roads) return null;
  const url = new URL(manifest.roads.url, manifest.baseUrl).href;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`road graph ${url}: HTTP ${res.status}`);
  return await res.json() as RoadGraphJson;
}
