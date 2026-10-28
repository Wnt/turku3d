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
  /** Optional surface Y per vertex, supplied by draft bridge decks. */
  heights?: Float64Array;
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

export interface RoadGraphJson {
  format: string; version: number; frame: string;
  bbox: [number, number, number, number];
  nodes: number[];
  edges: number[][];
  edgeHeights?: (number[] | null)[];
}

function checkFormat(json: RoadGraphJson) {
  if (json.format !== 'turku3d-roadgraph' || json.version !== 1 || json.frame !== 'engine-xz') {
    throw new Error(`road graph: unsupported ${json.format} v${json.version} (${json.frame})`);
  }
}

export function parseRoadGraph(json: RoadGraphJson): RoadGraph {
  checkFormat(json);
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
    const ys = json.edgeHeights?.[index];
    if (ys && (ys.length !== cumulative.length || !ys.every(Number.isFinite))) throw new Error(`road graph: bad heights ${index}`);
    return { a, b, flags, points, cumulative, ...(ys ? { heights: Float64Array.from(ys) } : {}) };
  });
  return { nodes, edges, adjacency, bbox: json.bbox };
}

export const EMPTY_ROAD_GRAPH: RoadGraph = { nodes: new Float64Array(), edges: [], adjacency: [], bbox: [0, 0, 0, 0] };

/** One graph from several: the published one and the draft tiles' (pipeline/turku3d/coarse roads.json). Each part is
 *  clipped to its own area, so a street leaving one part ends on its edge at the same point (to 0.1 m, the pipeline's
 *  node precision) where it enters the next: nodes merge by coordinate and the parts join there. An edge both parts
 *  carry (overlapping areas) is kept once. */
export function mergeRoadGraphs(parts: readonly RoadGraphJson[]): RoadGraph {
  if (!parts.length) return EMPTY_ROAD_GRAPH;
  if (parts.length === 1) return parseRoadGraph(parts[0]);
  const index = new Map<string, number[]>(), seen = new Set<string>();
  const nodes: number[] = [], edges: number[][] = [];
  const edgeHeights: (number[] | null)[] = [];
  const bbox: [number, number, number, number] = [Infinity, Infinity, -Infinity, -Infinity];
  const node = (x: number, z: number) => {
    // Quantisation cells are an index, not an equality test: a seam point can
    // straddle two rounding bins after independent tile clipping.
    const cx = Math.floor(x * 10), cz = Math.floor(z * 10);
    for (let dx = -2; dx <= 2; dx++) for (let dz = -2; dz <= 2; dz++) {
      for (const k of index.get(`${cx + dx},${cz + dz}`) ?? []) {
        if (Math.hypot(nodes[2 * k] - x, nodes[2 * k + 1] - z) <= 0.11) return k;
      }
    }
    const key = `${cx},${cz}`, k = nodes.length / 2;
    const bucket = index.get(key) ?? [];
    bucket.push(k); index.set(key, bucket); nodes.push(x, z);
    return k;
  };
  for (const part of parts) {
    checkFormat(part);
    const map = Array.from({ length: part.nodes.length / 2 }, (_, i) => node(part.nodes[2 * i], part.nodes[2 * i + 1]));
    for (const [i, row] of part.edges.entries()) {
      const edge = [map[row[0]], map[row[1]], ...row.slice(2)];
      const key = edge.map(v => Math.round(v * 10)).join(',');
      if (seen.has(key)) continue;
      seen.add(key);
      edges.push(edge);
      edgeHeights.push(part.edgeHeights?.[i] ?? null);
    }
    for (let k = 0; k < 4; k++) bbox[k] = k < 2 ? Math.min(bbox[k], part.bbox[k]) : Math.max(bbox[k], part.bbox[k]);
  }
  return parseRoadGraph({ format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox, nodes, edges, edgeHeights });
}

/** The published graph's JSON, or null when the tile build carries none (synthetic dev tiles). */
export async function loadRoadGraphJson(manifest: Pick<Manifest, 'roads' | 'baseUrl'>): Promise<RoadGraphJson | null> {
  if (!manifest.roads) return null;
  const url = new URL(manifest.roads.url, manifest.baseUrl).href;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`road graph ${url}: HTTP ${res.status}`);
  return await res.json() as RoadGraphJson;
}
