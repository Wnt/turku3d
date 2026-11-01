// The one adapter between a tile's roads.json and traffic. Everything downstream (tileNetwork.ts) sees only
// TrafficRoads: car edges with stable ids, optional per-vertex Y, lane counts per direction and a cruise speed.
//
// v1 (today's tiles) carries none of that, so the client fallback of the street-richness plan §3.6 applies:
// - stable id: a hash of the edge's endpoints quantised to 0.5 m in this tile's own data. A tile's ids therefore
//   never depend on load order or on neighbouring tiles (mergeRoadGraphs renumbers; this never reads it);
// - Y: none here; the physics worker samples the collision surface along each lane (bridge decks keep edgeHeights);
// - lanes: one per direction, half a lane width right of the centreline; a one-way street drives on it;
// - speed: by road class.
// G3's roads.json v2 (docs/specs/roads-json-v2.md on local/g3-roads) adds those fields; `fromV2` reads them.
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
  /** Lanes travelling a→b and b→a. */
  forward: number; backward: number;
  /** Cruise speed (m/s). */
  speed: number;
  cls: number;
}

export interface TrafficRoads {
  version: number;
  /** x,z pairs per node. */
  nodes: Float64Array;
  edges: TrafficEdge[];
  /** Nodes with traffic signals (G3); empty for v1. */
  signals: ReadonlySet<number>;
}

const q = (v: number) => Math.round(v * 2);

/** Traffic view of one tile's street graph. Throws on an unknown format: such a tile spawns nothing. */
export function trafficRoads(json: RoadGraphJson): TrafficRoads {
  if ((json as { version?: number }).version === 2) return fromV2(json as unknown as RoadsV2);
  const graph = parseRoadGraph(json);
  const edges: TrafficEdge[] = [];
  const seen = new Map<string, number>();
  for (const edge of graph.edges) {
    const cls = edgeClass(edge);
    if (!(edge.flags & CAR) || cls < TRAFFIC.minClass) continue;
    const p = edge.points, n = p.length / 2;
    const base = `${q(p[0])},${q(p[1])}>${q(p[2 * n - 2])},${q(p[2 * n - 1])}`;
    // Two ways between the same quantised endpoints (rare: a split carriageway stub) keep distinct, ordered ids.
    const k = seen.get(base) ?? 0; seen.set(base, k + 1);
    const oneway = (edge.flags & ONEWAY) !== 0;
    edges.push({ key: hex32(hash32('v1', base, k)), a: edge.a, b: edge.b, points: p, ys: edge.heights ?? null,
      forward: 1, backward: oneway ? 0 : 1, speed: TRAFFIC.classSpeed[cls], cls });
  }
  return { version: 1, nodes: graph.nodes, edges, signals: new Set() };
}

/** Provisional reading of G3's v2 rows, to be aligned with docs/specs/roads-json-v2.md when it is published:
 *  `edges[i] = {id, a, b, flags, points:[x,z,...], y?:[...], lanes?:[forward, backward], speed?:m/s}` and
 *  `signals?: node indices`. Missing fields fall back exactly as v1 does. */
interface RoadsV2 {
  format: string; version: 2; frame: string; bbox: [number, number, number, number];
  nodes: number[];
  edges: { id: string; a: number; b: number; flags: number; points: number[]; y?: number[]; lanes?: [number, number]; speed?: number }[];
  signals?: number[];
}

function fromV2(json: RoadsV2): TrafficRoads {
  if (json.format !== 'turku3d-roadgraph' || json.frame !== 'engine-xz' || !Array.isArray(json.edges)) {
    throw new Error(`traffic roads: unsupported ${json.format} v${json.version}`);
  }
  const nodes = Float64Array.from(json.nodes);
  const count = nodes.length / 2;
  const edges: TrafficEdge[] = [];
  for (const row of json.edges) {
    const cls = (row.flags >> 3) & 3;
    if (!(row.flags & CAR) || cls < TRAFFIC.minClass) continue;
    if (!(row.a >= 0 && row.a < count && row.b >= 0 && row.b < count) || typeof row.id !== 'string') {
      throw new Error('traffic roads: bad v2 edge');
    }
    const points = Float64Array.from(row.points);
    const oneway = (row.flags & ONEWAY) !== 0;
    const [forward, backward] = row.lanes ?? [1, oneway ? 0 : 1];
    const ys = row.y && row.y.length === points.length / 2 ? Float64Array.from(row.y) : null;
    edges.push({ key: hex32(hash32('v2', row.id)), a: row.a, b: row.b, points, ys,
      forward: Math.max(0, Math.min(4, forward | 0)), backward: Math.max(0, Math.min(4, backward | 0)),
      speed: row.speed && row.speed > 2 ? row.speed : TRAFFIC.classSpeed[cls], cls });
  }
  return { version: 2, nodes, edges, signals: new Set(json.signals ?? []) };
}
