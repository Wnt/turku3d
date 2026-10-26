// Street graph published by pipeline/turku3d/roads (manifest.roads, format turku3d-roadgraph v1).
import type { Manifest } from '../world/manifest';

export const FOOT = 1, CAR = 2, ONEWAY = 4;
const CLASS_SHIFT = 3;
export type TravelMode = 'foot' | 'car';
export const modeBit = (mode: TravelMode) => (mode === 'car' ? CAR : FOOT);

export interface RoadEdge {
  a: number;
  b: number;
  flags: number;
  /** x,z pairs from node a to node b, both ends included. */
  points: Float64Array;
  /** Cumulative polyline length at each point; the last value is the edge length. */
  cumulative: Float64Array;
}

export interface RoadGraph {
  /** x,z pairs per node. */
  nodes: Float64Array;
  edges: RoadEdge[];
  /** Edge indices touching each node. */
  adjacency: number[][];
  bbox: [number, number, number, number];
}

export const edgeLength = (edge: RoadEdge) => edge.cumulative[edge.cumulative.length - 1];
/** Road class 0 path, 1 service, 2 street, 3 major. */
export const edgeClass = (edge: RoadEdge) => (edge.flags >> CLASS_SHIFT) & 3;

interface RoadGraphJson {
  format: string; version: number; frame: string;
  bbox: [number, number, number, number];
  nodes: number[];
  edges: number[][];
}

export function parseRoadGraph(json: RoadGraphJson): RoadGraph {
  if (json.format !== 'turku3d-roadgraph' || json.version !== 1 || json.frame !== 'engine-xz') {
    throw new Error(`road graph: unsupported ${json.format} v${json.version} (${json.frame})`);
  }
  const nodes = Float64Array.from(json.nodes);
  const count = nodes.length / 2;
  const adjacency: number[][] = Array.from({ length: count }, () => []);
  const edges = json.edges.map((row, index): RoadEdge => {
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
    adjacency[a].push(index);
    if (b !== a) adjacency[b].push(index);
    return { a, b, flags, points, cumulative };
  });
  return { nodes, edges, adjacency, bbox: json.bbox };
}

export const EMPTY_ROAD_GRAPH: RoadGraph = { nodes: new Float64Array(), edges: [], adjacency: [], bbox: [0, 0, 0, 0] };

/** The published graph, or an empty one when the tile build carries none (synthetic dev tiles). */
export async function loadRoadGraph(manifest: Pick<Manifest, 'roads' | 'baseUrl'>): Promise<RoadGraph> {
  if (!manifest.roads) return EMPTY_ROAD_GRAPH;
  const url = new URL(manifest.roads.url, manifest.baseUrl).href;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`road graph ${url}: HTTP ${res.status}`);
  return parseRoadGraph(await res.json() as RoadGraphJson);
}
