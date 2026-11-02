// The one adapter between a tile's roads.json and traffic. It reads the tile through the shared road graph reader
// (gameplay/roadGraph.ts parseRoadGraph, revision 2 per docs/specs/roads-json-v2.md, with its v1 fallback) and
// keeps what traffic needs: car edges with stable keys, per-vertex Y where known, lane counts and width, a cruise
// speed, junction setbacks, seam nodes and signal controllers. Everything downstream (tileNetwork.ts) sees only
// TrafficRoads.
//
// Only this tile's own file is read, never the merged graph: a tile's lanes, phases and identities must not depend
// on which neighbours a client holds (seams are joined by traffic/network.ts instead).
//
// Revision 2 (G3) carries ids, lanes, widths, limits, OSM highway classes, car densities, heights on every edge,
// setbacks, SEAM node flags and signal controllers. A v1 file gets the reader's fallback (quantised-endpoint ids,
// 1+1 lanes, width and limit by class) and the plan §3.6 client fallback here: heights sampled from collision by
// the physics worker (bridge decks keep edgeHeights), seams found on the tile bbox, no signals. Every kept edge
// has a peak flow (traffic/density.ts) that sets how many of its slots carry cars.
import { CAR, EDGE, EVENT, NODE, edgeClass, parseRoadGraph, type RoadEdge, type RoadGraphJson, type RoadEvent } from '../gameplay/roadGraph';
import { PEAK_FLOW, streetFlow } from './density';
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
  /** Vehicles per hour per lane at the busiest hour (traffic/density.ts); > 0 on every kept edge. */
  flow: number;
  /** Distance from node a / node b where the junction box begins (m); 0 when unknown. */
  setbackA: number; setbackB: number;
  events: RoadEvent[];
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
  nodeFlags: Uint16Array;
}

/** Revision 2 streets whose `highway` the flow table does not know, by car density (spec §3.5 rules). */
const BY_DENSITY: readonly [number, string][] = [[13, 'primary'], [11, 'secondary'], [8, 'tertiary'], [5, 'unclassified'], [3, 'residential']];
/** Share of the speed limit that ambient cars cruise at in town. */
const LIMIT_SHARE = 0.85;
/** A signal stop position this close to an edge end belongs to the junction there (m). */
const SIGNAL_REACH = 40;

/** Traffic view of one tile's street graph. Throws on an unknown format: such a tile spawns nothing. */
export function trafficRoads(json: RoadGraphJson): TrafficRoads {
  const graph = parseRoadGraph(json);
  const revision = (json.revision ?? 1) >= 2 && json.edgeIds ? 2 : 1;
  const signals = new Map<number, string>();
  graph.nodeFlags.forEach((flags, node) => { if (flags & NODE.SIGNALS) signals.set(node, ''); });
  const edges: TrafficEdge[] = [];
  const seen = new Map<string, number>();
  for (const edge of graph.edges) {
    if (!(edge.flags & CAR) || edge.carDensity <= 0 || edge.eflags & EDGE.PSV_ONLY) continue;
    const flow = edgeFlow(edge, revision);
    if (!(flow > 0)) continue;
    const forward = Math.max(0, Math.min(4, edge.lanes.fwd | 0)), backward = Math.max(0, Math.min(4, edge.lanes.bwd | 0));
    if (!forward && !backward) continue;
    const [s0, s1, logical] = edge.span;
    // A row is its logical edge's id and span; two rows sharing both (never in valid data) stay distinct.
    const base = `${edge.id}|${Math.round(s0 * 10)}`;
    const k = seen.get(base) ?? 0; seen.set(base, k + 1);
    const length = edge.cumulative[edge.cumulative.length - 1];
    edges.push({ key: hex32(hash32('road', base, k)), a: edge.a, b: edge.b, points: edge.points, ys: edge.heights ?? null,
      forward, backward, width: Math.max(3, edge.lanes.width), speed: Math.max(3, edge.kmh / 3.6 * LIMIT_SHARE), flow,
      // Setbacks are measured from the logical edge's ends; they apply where this row ends there.
      setbackA: s0 < 0.01 ? edge.setbacks[0] : 0, setbackB: Math.abs(s1 - logical) < 0.01 ? edge.setbacks[1] : 0,
      events: edge.events });
    // A signal's stop position near an end puts the junction there under its controller.
    for (const event of edge.events) {
      if ((event.code & 15) !== EVENT.SIGNAL) continue;
      const controller = graph.controllers[event.controller]?.id ?? '';
      if (event.code & EVENT.FORWARD && length - event.s < SIGNAL_REACH) signals.set(edge.b, controller);
      if (event.code & EVENT.BACKWARD && event.s < SIGNAL_REACH) signals.set(edge.a, controller);
    }
  }
  const seams = revision === 2 ? new Set([...graph.nodeFlags.keys()].filter(n => graph.nodeFlags[n] & NODE.SEAM)) : null;
  return { revision, nodes: graph.nodes, edges, seams, signals, nodeFlags: graph.nodeFlags };
}

/** Peak flow of one edge: by OSM `highway` in revision 2 (service roads, aisles and driveways none), by class in v1
 *  (which cannot tell a living street from a residential one), times the area factor at its midpoint. */
function edgeFlow(edge: RoadEdge, revision: number): number {
  const p = edge.points, x = (p[0] + p[p.length - 2]) / 2, z = (p[1] + p[p.length - 1]) / 2;
  if (revision < 2) return streetFlow(null, edgeClass(edge), x, z);
  const highway = edge.highway.replace(/_link$/, '');
  if (highway === 'service') return 0;
  const known = highway in PEAK_FLOW ? highway : BY_DENSITY.find(([d]) => edge.carDensity >= d)?.[1];
  return known ? streetFlow(known, 0, x, z) : 0;
}
