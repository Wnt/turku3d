// Client street-lamp rule: a lamp about every 30 m along each tile's CAR streets (roads.json, class >= 1).
// Generator phase G1 replaces this with register lamps; until then it is ordinary client code, not a flag.
//
// Determinism: a tile's lamps derive only from that tile's own roads.json (and its own visual model, tileScan.ts),
// never from the merged graph or from neighbouring tiles. Every client therefore places identical lamps whatever
// the load order. Seams stay regular because each chain ends half a spacing from its end node: a street clipped at
// a seam has its last lamp S/2 before it in one tile and its first lamp S/2 after it in the next.
import { CAR, ONEWAY, edgeClass, parseRoadGraph, type RoadEdge, type RoadGraphJson } from '../../gameplay/roadGraph';
import { VISUAL_L0 } from '../../core/origin';

export const LAMP_RULE = {
  /** Target spacing along a chain, metres. */
  spacing: 30,
  /** Kerb offset from the centreline by class (index 1..3), two-way and one-way. roads.json carries no widths. */
  offset: [0, 3.2, 4.6, 7.0] as const,
  onewayOffset: [0, 2.4, 3.4, 4.6] as const,
  /** Retreat steps toward the centreline when the kerb point is blocked (building, water, tunnel). */
  retreat: [0, 1, 2] as const,
  minOffset: 1.8,
  /** Lamps of one tile closer than this to a kept lamp are dropped (junction corners). */
  minSeparation: 8,
  /** Canonical chain orientation: d·w >= 0. Tilted off the axes so grid streets are never ambiguous. */
  canonical: [Math.cos(0.35), Math.sin(0.35)] as const,
  seamTolerance: 0.15,
} as const;

export type Bounds = readonly [number, number, number, number];

/** A lamp station on a street centreline, before its kerb point is chosen against the tile's surfaces. */
export interface Station {
  x: number; z: number;
  /** Unit tangent of the chain at the station (canonical orientation). */
  tx: number; tz: number;
  cls: number;
  oneway: boolean;
  /** Sides to try, in order: +1 is the right-hand normal (-tz, tx) of the canonical tangent. */
  sides: readonly (1 | -1)[];
  offset: number;
  /** Surveyed deck height at the centreline (bridge decks carry edgeHeights), else null. */
  deckY: number | null;
}

export function tileBounds(id: string): Bounds | null {
  const m = /^t0_(-?\d+)_(-?\d+)$/.exec(id);
  if (!m) return null;
  const i = Number(m[1]), j = Number(m[2]);
  return [i * VISUAL_L0, j * VISUAL_L0, (i + 1) * VISUAL_L0, (j + 1) * VISUAL_L0];
}

/** Half-open ownership: a point on a shared seam belongs to exactly one tile. */
export const owns = (b: Bounds, x: number, z: number) => x >= b[0] && x < b[2] && z >= b[1] && z < b[3];

const lit = (edge: RoadEdge) => (edge.flags & CAR) !== 0 && edgeClass(edge) >= 1;

interface Chain {
  points: number[];
  heights: (number | null)[];
  /** Travel direction of each segment relative to the chain (one-way streets only): +1 along, -1 against. */
  travel: number[];
  cls: number; oneway: boolean;
  start: number; end: number;
}

/** Chains join CAR edges through plain degree-2 nodes of equal class and one-way state; seams and junctions end them. */
function chains(json: RoadGraphJson, bounds: Bounds): Chain[] {
  const graph = parseRoadGraph(json);
  const { nodes, edges, adjacency } = graph;
  const t = LAMP_RULE.seamTolerance;
  const seam = (n: number) => {
    const x = nodes[2 * n], z = nodes[2 * n + 1];
    return Math.abs(x - bounds[0]) < t || Math.abs(x - bounds[2]) < t || Math.abs(z - bounds[1]) < t || Math.abs(z - bounds[3]) < t;
  };
  const incident = adjacency.map(list => list.filter(e => lit(edges[e])));
  const key = (e: RoadEdge) => `${edgeClass(e)}/${e.flags & ONEWAY}`;
  const through = (n: number) => {
    const list = incident[n];
    return list.length === 2 && list[0] !== list[1] && !seam(n) && key(edges[list[0]]) === key(edges[list[1]]);
  };
  const visited = new Set<number>();
  const out: Chain[] = [];
  const walk = (from: number, first: number) => {
    const edge0 = edges[first];
    const chain: Chain = { points: [], heights: [], travel: [], cls: edgeClass(edge0), oneway: (edge0.flags & ONEWAY) !== 0, start: from, end: from };
    let node = from, e = first;
    for (;;) {
      visited.add(e);
      const edge = edges[e];
      const forward = edge.a === node;
      const n = edge.points.length / 2;
      for (let i = chain.points.length ? 1 : 0; i < n; i++) {
        const k = forward ? i : n - 1 - i;
        chain.points.push(edge.points[2 * k], edge.points[2 * k + 1]);
        chain.heights.push(edge.heights ? edge.heights[k] : null);
        if (i > 0) chain.travel.push(forward ? 1 : -1);
      }
      node = forward ? edge.b : edge.a;
      chain.end = node;
      if (node === from || !through(node)) break;
      const next = incident[node].find(other => other !== e && !visited.has(other));
      if (next === undefined) break;
      e = next;
    }
    out.push(chain);
  };
  for (let n = 0; n < incident.length; n++) {
    if (through(n)) continue;
    for (const e of incident[n]) if (!visited.has(e)) walk(n, e);
  }
  // Closed loops of plain nodes (a roundabout fully inside the tile): start at the lowest node.
  for (let e = 0; e < edges.length; e++) {
    if (!lit(edges[e]) || visited.has(e)) continue;
    const { a, b } = edges[e];
    const lower = nodes[2 * a] < nodes[2 * b] || (nodes[2 * a] === nodes[2 * b] && nodes[2 * a + 1] <= nodes[2 * b + 1]) ? a : b;
    walk(lower, e);
  }
  return out.map(chain => orient(chain));
}

function orient(chain: Chain): Chain {
  const p = chain.points, n = p.length / 2;
  const [wx, wz] = LAMP_RULE.canonical;
  if ((p[2 * n - 2] - p[0]) * wx + (p[2 * n - 1] - p[1]) * wz >= 0) return chain;
  const points: number[] = [];
  for (let i = n - 1; i >= 0; i--) points.push(p[2 * i], p[2 * i + 1]);
  return { ...chain, points, heights: [...chain.heights].reverse(), travel: chain.travel.map(v => -v).reverse(),
    start: chain.end, end: chain.start };
}

/** The seam parity base: a seam on the tile's max edge counts 0, on its min edge 1, so sides keep alternating across. */
function seamBase(x: number, z: number, b: Bounds): number | null {
  const t = LAMP_RULE.seamTolerance;
  if (Math.abs(x - b[2]) < t || Math.abs(z - b[3]) < t) return 0;
  if (Math.abs(x - b[0]) < t || Math.abs(z - b[1]) < t) return 1;
  return null;
}

/** Lamp stations of one tile's street graph, in a deterministic order. */
export function lampStations(json: RoadGraphJson, bounds: Bounds = json.bbox): Station[] {
  const S = LAMP_RULE.spacing;
  const stations: Station[] = [];
  for (const chain of chains(json, bounds)) {
    const p = chain.points, n = p.length / 2;
    const cumulative = [0];
    for (let i = 1; i < n; i++) cumulative.push(cumulative[i - 1] + Math.hypot(p[2 * i] - p[2 * i - 2], p[2 * i + 1] - p[2 * i - 1]));
    const length = cumulative[n - 1];
    const count = Math.round(length / S);
    if (count < 1) continue;
    const startBase = seamBase(p[0], p[1], bounds), endBase = seamBase(p[2 * n - 2], p[2 * n - 1], bounds);
    const alternate = chain.cls === 3 && !chain.oneway;
    const offset = (chain.oneway ? LAMP_RULE.onewayOffset : LAMP_RULE.offset)[chain.cls];
    let segment = 0;
    for (let k = 0; k < count; k++) {
      const s = (k + 0.5) * length / count;
      while (segment < n - 2 && cumulative[segment + 1] < s) segment++;
      const span = cumulative[segment + 1] - cumulative[segment] || 1;
      const f = Math.min(1, Math.max(0, (s - cumulative[segment]) / span));
      const ax = p[2 * segment], az = p[2 * segment + 1], bx = p[2 * segment + 2], bz = p[2 * segment + 3];
      const tx = (bx - ax) / span, tz = (bz - az) / span;
      const ha = chain.heights[segment], hb = chain.heights[segment + 1];
      let side: 1 | -1 = 1;
      if (chain.oneway) side = chain.travel[segment] >= 0 ? 1 : -1;
      else if (alternate) {
        const parity = startBase !== null ? k + startBase : endBase !== null ? count - 1 - k + endBase : k;
        side = parity % 2 === 0 ? 1 : -1;
      }
      stations.push({ x: ax + (bx - ax) * f, z: az + (bz - az) * f, tx, tz, cls: chain.cls, oneway: chain.oneway,
        sides: [side, side === 1 ? -1 : 1], offset, deckY: ha !== null && hb !== null ? ha + (hb - ha) * f : null });
    }
  }
  return stations;
}

/** Kerb point of a station for one side and retreat step (right-hand normal is (-tz, tx)). */
export function kerbPoint(station: Station, side: 1 | -1, retreat: number) {
  const offset = Math.max(LAMP_RULE.minOffset, station.offset - retreat);
  return { x: station.x - station.tz * side * offset, z: station.z + station.tx * side * offset, offset };
}

/** Deterministic 32-bit hash of a world position at 0.5 m, for per-lamp variation. */
export function positionHash(x: number, z: number): number {
  let h = Math.imul(Math.round(x * 2) | 0, 0x9e3779b1) ^ Math.imul(Math.round(z * 2) | 0, 0x85ebca77);
  h ^= h >>> 15; h = Math.imul(h, 0x2c1b3c6d); h ^= h >>> 12; h = Math.imul(h, 0x297a2d39); h ^= h >>> 15;
  return h >>> 0;
}
