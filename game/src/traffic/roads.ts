// The one adapter between a tile's roads.json and traffic. Everything downstream (tileNetwork.ts) sees only
// TrafficRoads: car edges with stable ids, per-vertex Y where known, lane counts and width, a cruise speed,
// junction setbacks, seam nodes and signal controllers.
//
// Revision 2 (G3, docs/specs/roads-json-v2.md on local/g3-roads) carries all of that: `edgeIds`, `edgeAttrs`
// (lanes, width, kmh, car density, setbacks, flags), `edgeHeights` on every edge, `nodeFlags` (SEAM, SIGNALS)
// and SIGNAL events with `controllers`. It keeps `version: 1`, so its geometry parses as v1.
//
// A plain v1 file (today's tiles) gets the client fallback of the street-richness plan §3.6:
// - stable id: a hash of the edge's endpoints quantised to 0.5 m in this tile's own data, so a tile's ids never
//   depend on load order or on neighbouring tiles (mergeRoadGraphs renumbers; this never reads it);
// - Y: none here; the physics worker samples the collision surface along each lane (bridge decks keep edgeHeights);
// - lanes: one per direction, half a lane width right of the centreline; a one-way street drives on it;
// - speed by road class; seams found geometrically on the tile bbox; no signals.
import { CAR, ONEWAY, edgeClass, parseRoadGraph, type RoadGraphJson } from '../gameplay/roadGraph';
import { TRAFFIC } from './constants';
import { hash32, hex32 } from './hash';

export interface TrafficEdge {
  /** Stable within the tile's data, independent of load order. */
  key: string;
  a: number; b: number;
  /** x,z pairs from node a to node b, both ends included. */
  points: Float64Array;
  /** Surface Y per vertex when the data has it. */
  ys: Float64Array | null;
  /** Lanes travelling a→b and b→a, and the carriageway width they share (m). */
  forward: number; backward: number; width: number;
  /** Cruise speed (m/s). */
  speed: number;
  /** Distance from node a / node b where the junction box begins (m); 0 when unknown. */
  setbackA: number; setbackB: number;
}

export interface TrafficRoads {
  /** Data revision: 1 (client fallback) or 2 (G3). */
  revision: number;
  /** x,z pairs per node. */
  nodes: Float64Array;
  edges: TrafficEdge[];
  /** Seam nodes as the data marks them (revision 2), or null: decide geometrically on the bbox. */
  seams: ReadonlySet<number> | null;
  /** Signalised junction nodes with their controller id ('' when the data names none). */
  signals: ReadonlyMap<number, string>;
}

/** roads.json revision 2 fields (docs/specs/roads-json-v2.md §3). */
interface RevisionTwo {
  revision?: number;
  edgeIds?: string[];
  edgeAttrColumns?: string[];
  edgeAttrs?: number[][];
  nodeFlags?: number[];
  edgeEvents?: (number[] | null)[];
  controllers?: [string, number, number][];
}

const NODE_SIGNALS = 2, NODE_SEAM = 32;
const EDGE_PSV_ONLY = 4096;
const EVENT_SIGNAL = 1, EVENT_FORWARD = 16, EVENT_BACKWARD = 32;
/** Revision 2 car density (0..15) below which a street carries no ambient traffic: service roads (2), parking
 *  aisles and driveways (1) and living streets (1) stay quiet, like v1's class filter. */
const MIN_CAR_DENSITY = 3;
/** Share of the speed limit that ambient cars cruise at in town. */
const LIMIT_SHARE = 0.85;
/** A signal stop position this close to an edge end belongs to the junction there (m). */
const SIGNAL_REACH = 40;

const q = (v: number) => Math.round(v * 2);

/** Traffic view of one tile's street graph. Throws on an unknown format: such a tile spawns nothing. */
export function trafficRoads(json: RoadGraphJson): TrafficRoads {
  const graph = parseRoadGraph(json);
  const extra = json as RoadGraphJson & RevisionTwo;
  if ((extra.revision ?? 1) >= 2 && extra.edgeIds && extra.edgeAttrs && extra.edgeAttrColumns) return revisionTwo(graph, extra);
  const edges: TrafficEdge[] = [];
  const seen = new Map<string, number>();
  for (const edge of graph.edges) {
    const cls = edgeClass(edge);
    if (!(edge.flags & CAR) || cls < TRAFFIC.minClass) continue;
    const p = edge.points, n = p.length / 2;
    const base = `${q(p[0])},${q(p[1])}>${q(p[2 * n - 2])},${q(p[2 * n - 1])}`;
    // Two ways between the same quantised endpoints (rare: a split carriageway stub) keep distinct, ordered ids.
    const k = seen.get(base) ?? 0; seen.set(base, k + 1);
    const backward = edge.flags & ONEWAY ? 0 : 1;
    edges.push({ key: hex32(hash32('v1', base, k)), a: edge.a, b: edge.b, points: p, ys: edge.heights ?? null,
      forward: 1, backward, width: TRAFFIC.laneWidth * (1 + backward), speed: TRAFFIC.classSpeed[cls], setbackA: 0, setbackB: 0 });
  }
  return { revision: 1, nodes: graph.nodes, edges, seams: null, signals: new Map() };
}

function revisionTwo(graph: ReturnType<typeof parseRoadGraph>, json: RevisionTwo): TrafficRoads {
  const columns = json.edgeAttrColumns!;
  const col = (name: string) => {
    const i = columns.indexOf(name);
    if (i < 0) throw new Error(`traffic roads: edgeAttrs lacks ${name}`);
    return i;
  };
  const c = { s0: col('s0'), s1: col('s1'), length: col('length'), fwd: col('lanes_fwd'), bwd: col('lanes_bwd'), width: col('width'),
    kmh: col('kmh'), density: col('car_density'), flags: col('flags'), set0: col('setback0'), set1: col('setback1') };
  const ids = json.edgeIds!, attrs = json.edgeAttrs!;
  if (ids.length !== graph.edges.length || attrs.length !== graph.edges.length) throw new Error('traffic roads: v2 arrays do not match edges');
  const nodeFlags = json.nodeFlags ?? [];
  const seams = new Set<number>(), signals = new Map<number, string>();
  nodeFlags.forEach((flags, node) => { if (flags & NODE_SEAM) seams.add(node); if (flags & NODE_SIGNALS) signals.set(node, ''); });
  const edges: TrafficEdge[] = [];
  graph.edges.forEach((edge, i) => {
    const row = attrs[i];
    if (!(edge.flags & CAR) || row[c.density] < MIN_CAR_DENSITY || row[c.flags] & EDGE_PSV_ONLY) return;
    const forward = Math.max(0, Math.min(4, row[c.fwd] | 0)), backward = Math.max(0, Math.min(4, row[c.bwd] | 0));
    if (!forward && !backward) return;
    const length = edge.cumulative[edge.cumulative.length - 1];
    // Setbacks are measured from the logical edge's ends; they apply where this row ends there.
    const atStart = row[c.s0] < 0.01, atEnd = Math.abs(row[c.s1] - row[c.length]) < 0.01;
    edges.push({ key: hex32(hash32('v2', ids[i], Math.round(row[c.s0] * 10))), a: edge.a, b: edge.b, points: edge.points,
      ys: edge.heights ?? null, forward, backward, width: Math.max(3, row[c.width]),
      speed: Math.max(3, row[c.kmh] / 3.6 * LIMIT_SHARE),
      setbackA: atStart ? row[c.set0] : 0, setbackB: atEnd ? row[c.set1] : 0 });
    // A signal's stop position near an end puts that end's junction under its controller.
    const events = json.edgeEvents?.[i];
    for (let k = 0; events && k + 2 < events.length + 1; k += 3) {
      const [s, code, ref] = [events[k], events[k + 1], events[k + 2]];
      if ((code & 15) !== EVENT_SIGNAL) continue;
      const controller = ref >= 0 ? json.controllers?.[ref]?.[0] ?? '' : '';
      if (code & EVENT_FORWARD && length - s < SIGNAL_REACH) signals.set(edge.b, controller);
      if (code & EVENT_BACKWARD && s < SIGNAL_REACH) signals.set(edge.a, controller);
    }
  });
  return { revision: 2, nodes: graph.nodes, edges, seams, signals };
}
