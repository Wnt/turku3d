// The walkable FOOT network of the resident draft tiles, built so that every client derives the same walkers from it:
// nothing depends on tile load order or on mergeRoadGraphs' edge numbering. Positions are the pipeline's 0.1 m lattice;
// nodes, pieces and turn candidates are keyed and ordered by quantised world coordinates.
import { CAR, FOOT, type RoadGraphJson } from '../../gameplay/roadGraph';
import { VISUAL_L0 } from '../../core/origin';

/** Birth cells: a tenth of a 250 m tile, so no cell straddles a tile seam. */
export const CELL_M = VISUAL_L0 / 10;
/** Pipeline node precision (turku3d.roads PRECISION = 0.1 m). */
const Q = 10;

/** 32-bit integer hash (murmur3 mixing) of int32 values; identical in every JavaScript engine. */
export function hash32(...values: number[]): number {
  let h = 0x9e3779b9 | 0;
  for (const value of values) {
    let k = Math.imul(value | 0, 0xcc9e2d51);
    k = (k << 15) | (k >>> 17);
    h ^= Math.imul(k, 0x1b873593);
    h = (h << 13) | (h >>> 19);
    h = (Math.imul(h, 5) + 0xe6546b64) | 0;
  }
  h ^= values.length;
  h ^= h >>> 16; h = Math.imul(h, 0x85ebca6b); h ^= h >>> 13; h = Math.imul(h, 0xc2b2ae35); h ^= h >>> 16;
  return h >>> 0;
}
export const unit = (h: number) => h / 4294967296;

/** A path between decision points (junctions, dead ends, seams of missing tiles), oriented canonically. */
export interface Chain {
  /** Stable id: hash of the quantised polyline in canonical direction. */
  id: number;
  a: number; b: number;
  /** x,z pairs from node a to node b. */
  points: Float64Array;
  cumulative: Float64Array;
  length: number;
  /** Surveyed deck Y per point (draft bridges), NaN elsewhere; null when the chain has none. */
  heights: Float64Array | null;
  /** Road-graph flags of its edges (chains never join edges with different flags). */
  flags: number;
}

export interface WalkNode {
  x: number; z: number;
  /** hash32 of the quantised coordinates. */
  key: number;
  /** Chain ends leaving this node, ordered by departure direction (seam-independent). */
  exits: { chain: number; atStart: boolean }[];
  /** Every tile touching the node is resident: its degree is final. */
  complete: boolean;
}

/** The part of one network segment inside a birth cell: chain arc [s0, s0 + length]. `forward` is true when the
 *  chain runs from the piece's smaller quantised end to its larger one (births are placed from the smaller end). */
export interface Piece { chain: number; s0: number; length: number; forward: boolean; weight: number }
export interface Cell { cx: number; cz: number; pieces: Piece[]; weighted: number }

export interface WalkNetwork {
  nodes: WalkNode[];
  chains: Chain[];
  cells: Map<number, Cell>;
  /** Resident tile rectangles (x0, z0, x1, z1). */
  areas: [number, number, number, number][];
}

export const cellKey = (cx: number, cz: number) => hash32(cx, cz);

/** Walker density weight per metre of FOOT-only way, by its own class and the busiest street in the same cell. */
export function pieceWeight(footClass: number, carClass: number): number {
  if (footClass >= 1) return 1.6; // pedestrianised service/residential street
  return [0.7, 0.85, 1.0, 1.4][carClass] ?? 1.4;
}

/** Pseudo-angle of (dx, dz) in [0, 4): monotonic in the true angle, exact rational arithmetic. */
function pseudoAngle(dx: number, dz: number): number {
  const p = dx / (Math.abs(dx) + Math.abs(dz));
  return dz < 0 ? 3 + p : 1 - p;
}

interface RawEdge { pts: number[]; flags: number; heights: number[] | null }

const packed = (ix: number, iz: number) => `${ix},${iz}`;

/** Builds the network from the resident tiles' road graphs (the published graph or drafts' roads.json). */
export function buildWalkNetwork(parts: readonly RoadGraphJson[]): WalkNetwork {
  const areas = parts.map(p => [...p.bbox] as [number, number, number, number]);
  const resident = (x: number, z: number) => {
    // Every L0 tile whose closed square contains the point must lie inside a resident part.
    const tx = x / VISUAL_L0, tz = z / VISUAL_L0;
    const xs = Number.isInteger(tx) ? [tx - 1, tx] : [Math.floor(tx)], zs = Number.isInteger(tz) ? [tz - 1, tz] : [Math.floor(tz)];
    return xs.every(i => zs.every(j => areas.some(([x0, z0, x1, z1]) =>
      x0 <= i * VISUAL_L0 + 1e-6 && z0 <= j * VISUAL_L0 + 1e-6 && x1 >= (i + 1) * VISUAL_L0 - 1e-6 && z1 >= (j + 1) * VISUAL_L0 - 1e-6)));
  };

  // FOOT-only edges on the 0.1 m lattice, deduplicated by geometry. CAR edges only lend their class to cells.
  const edges: RawEdge[] = [], seen = new Set<string>();
  const carClass = new Map<number, number>();
  for (const part of parts) {
    const n = part.nodes;
    part.edges.forEach((row, index) => {
      const [a, b, flags] = row;
      const xs = [n[2 * a], n[2 * a + 1], ...row.slice(4), n[2 * b], n[2 * b + 1]];
      const pts = xs.map(v => Math.round(v * Q));
      if (flags & CAR) {
        const cls = (flags >> 3) & 3;
        for (let i = 0; i + 3 < pts.length; i += 2) forCells(pts[i] / Q, pts[i + 1] / Q, pts[i + 2] / Q, pts[i + 3] / Q, (cx, cz) => {
          const key = cellKey(cx, cz);
          carClass.set(key, Math.max(carClass.get(key) ?? 0, cls));
        });
        return;
      }
      if (!(flags & FOOT) || pts.length < 4) return;
      const forward = pts.join(','), backward = reversePairs(pts).join(',');
      const key = `${flags & ~4}|${forward < backward ? forward : backward}`;
      if (seen.has(key)) return;
      seen.add(key);
      const heights = part.edgeHeights?.[index] ?? null;
      edges.push({ pts, flags, heights: heights && heights.length === pts.length / 2 ? heights : null });
    });
  }

  // Canonical nodes: endpoints within one lattice step (as mergeRoadGraphs' 0.11 m) are one node, represented by
  // the smallest member, whatever order the parts arrived in.
  const parent = new Map<string, string>();
  const find = (k: string): string => { let r = k; while (parent.get(r) !== r) r = parent.get(r)!; parent.set(k, r); return r; };
  const less = (a: string, b: string) => { const [ax, az] = a.split(',').map(Number), [bx, bz] = b.split(',').map(Number); return ax < bx || (ax === bx && az < bz); };
  for (const e of edges) for (const i of [0, e.pts.length - 2]) parent.set(packed(e.pts[i], e.pts[i + 1]), packed(e.pts[i], e.pts[i + 1]));
  for (const k of parent.keys()) {
    const [ix, iz] = k.split(',').map(Number);
    for (const other of [packed(ix + 1, iz), packed(ix, iz + 1)]) {
      if (!parent.has(other)) continue;
      const ra = find(k), rb = find(other);
      if (ra !== rb) { if (less(ra, rb)) parent.set(rb, ra); else parent.set(ra, rb); }
    }
  }
  const nodeIndex = new Map<string, number>(), nodes: WalkNode[] = [];
  const nodeOf = (ix: number, iz: number) => {
    const root = find(packed(ix, iz));
    let k = nodeIndex.get(root);
    if (k === undefined) {
      const [rx, rz] = root.split(',').map(Number);
      k = nodes.length; nodeIndex.set(root, k);
      nodes.push({ x: rx / Q, z: rz / Q, key: hash32(rx, rz), exits: [], complete: resident(rx / Q, rz / Q) });
    }
    return k;
  };
  const ends = edges.map(e => {
    const a = nodeOf(e.pts[0], e.pts[1]), b = nodeOf(e.pts[e.pts.length - 2], e.pts[e.pts.length - 1]);
    // Snap the polyline ends onto the canonical node.
    e.pts[0] = Math.round(nodes[a].x * Q); e.pts[1] = Math.round(nodes[a].z * Q);
    e.pts[e.pts.length - 2] = Math.round(nodes[b].x * Q); e.pts[e.pts.length - 1] = Math.round(nodes[b].z * Q);
    return [a, b] as const;
  });
  const incident: number[][] = nodes.map(() => []);
  ends.forEach(([a, b], i) => { incident[a].push(i); if (b !== a) incident[b].push(i); else incident[a].push(i); });

  // Chains join edges through complete pass-through nodes with matching flags.
  const passThrough = (k: number) => nodes[k].complete && incident[k].length === 2 && incident[k][0] !== incident[k][1]
    && edges[incident[k][0]].flags === edges[incident[k][1]].flags;
  const used = new Uint8Array(edges.length), chains: Chain[] = [];
  const walk = (start: number, first: number) => {
    const pts: number[] = [], hs: number[] = [];
    let node = start, edge = first, anyHeight = false;
    for (;;) {
      used[edge] = 1;
      const e = edges[edge], forward = ends[edge][0] === node && (ends[edge][1] !== node || pts.length === 0);
      const seq = forward ? e.pts : reversePairs(e.pts);
      const h = e.heights ? (forward ? e.heights : [...e.heights].reverse()) : null;
      if (h) anyHeight = true;
      for (let i = pts.length ? 2 : 0; i < seq.length; i += 2) { pts.push(seq[i], seq[i + 1]); hs.push(h?.[i / 2] ?? NaN); }
      node = forward ? ends[edge][1] : ends[edge][0];
      if (!passThrough(node) || node === start) break;
      const next = incident[node].find(i => i !== edge)!;
      if (used[next]) break;
      edge = next;
    }
    return { pts, hs: anyHeight ? hs : null, end: node };
  };
  const add = (start: number, first: number) => {
    const { pts, hs, end } = walk(start, first);
    const forward = pts.join(','), backward = reversePairs(pts).join(',');
    const flip = backward < forward;
    const seq = flip ? reversePairs(pts) : pts;
    const points = Float64Array.from(seq, v => v / Q);
    const cumulative = new Float64Array(points.length / 2);
    for (let i = 1; i < cumulative.length; i++) {
      const dx = points[2 * i] - points[2 * i - 2], dz = points[2 * i + 1] - points[2 * i - 1];
      cumulative[i] = cumulative[i - 1] + Math.sqrt(dx * dx + dz * dz);
    }
    const id = hash32(...seq);
    chains.push({ id, a: flip ? end : start, b: flip ? start : end, points, cumulative, length: cumulative[cumulative.length - 1],
      heights: hs ? Float64Array.from(flip ? [...hs].reverse() : hs) : null, flags: edges[first].flags });
  };
  nodes.forEach((_, k) => { if (!passThrough(k)) for (const e of incident[k]) if (!used[e]) add(k, e); });
  // Closed loops of pass-through nodes start at their smallest node.
  const order = nodes.map((_, k) => k).sort((p, q) => nodes[p].x - nodes[q].x || nodes[p].z - nodes[q].z);
  for (const k of order) for (const e of incident[k]) if (!used[e]) add(k, e);

  // Exits ordered by the direction of the first segment: unchanged when a chain is joined across a seam later.
  chains.forEach((chain, c) => {
    nodes[chain.a].exits.push({ chain: c, atStart: true });
    nodes[chain.b].exits.push({ chain: c, atStart: false });
  });
  for (const node of nodes) {
    const angle = (exit: { chain: number; atStart: boolean }) => {
      const p = chains[exit.chain].points, n = p.length;
      return exit.atStart ? pseudoAngle(p[2] - p[0], p[3] - p[1]) : pseudoAngle(p[n - 4] - p[n - 2], p[n - 3] - p[n - 1]);
    };
    node.exits.sort((u, v) => angle(u) - angle(v) || chains[u.chain].id - chains[v.chain].id || Number(v.atStart) - Number(u.atStart));
  }

  // Birth pieces per cell, ordered by their own quantised geometry (independent of how chains were joined).
  const cells = new Map<number, Cell>(), sorted = new Map<number, { key: number[]; piece: Piece }[]>();
  chains.forEach((chain, c) => {
    const p = chain.points, cls = (chain.flags >> 3) & 3;
    for (let i = 0; i + 1 < p.length / 2; i++) {
      // Clip each segment from its smaller lattice end, whatever the chain's orientation, so pieces are bit-identical
      // however the chain was joined.
      const ix0 = Math.round(p[2 * i] * Q), iz0 = Math.round(p[2 * i + 1] * Q), ix1 = Math.round(p[2 * i + 2] * Q), iz1 = Math.round(p[2 * i + 3] * Q);
      if (ix0 === ix1 && iz0 === iz1) continue;
      const swap = ix0 > ix1 || (ix0 === ix1 && iz0 > iz1);
      const sx = (swap ? ix1 : ix0) / Q, sz = (swap ? iz1 : iz0) / Q, ex = (swap ? ix0 : ix1) / Q, ez = (swap ? iz0 : iz1) / Q;
      const len = Math.sqrt((ex - sx) ** 2 + (ez - sz) ** 2), arc = chain.cumulative[i + 1] - chain.cumulative[i];
      forCells(sx, sz, ex, ez, (cx, cz, t0, t1) => {
        if (t1 - t0 < 1e-9) return;
        const key = cellKey(cx, cz);
        let cell = cells.get(key);
        if (!cell) { cell = { cx, cz, pieces: [], weighted: 0 }; cells.set(key, cell); sorted.set(key, []); }
        const order = [sx + (ex - sx) * t0, sz + (ez - sz) * t0, sx + (ex - sx) * t1, sz + (ez - sz) * t1].map(v => Math.round(v * 100));
        sorted.get(key)!.push({ key: order, piece: { chain: c, s0: chain.cumulative[i] + arc * (swap ? 1 - t1 : t0),
          length: len * (t1 - t0), forward: !swap, weight: pieceWeight(cls, carClass.get(key) ?? 0) } });
      });
    }
  });
  for (const [key, cell] of cells) {
    const list = sorted.get(key)!.sort((u, v) => u.key[0] - v.key[0] || u.key[1] - v.key[1] || u.key[2] - v.key[2] || u.key[3] - v.key[3]);
    cell.pieces = list.map(e => e.piece);
    for (const piece of cell.pieces) cell.weighted += piece.length * piece.weight;
  }
  return { nodes, chains, cells, areas };
}

function reversePairs(pts: number[]): number[] {
  const out = new Array<number>(pts.length);
  for (let i = 0; i < pts.length; i += 2) { out[pts.length - 2 - i] = pts[i]; out[pts.length - 1 - i] = pts[i + 1]; }
  return out;
}

/** Calls back with each birth cell the segment crosses and the segment parameter range inside it. */
export function forCells(x0: number, z0: number, x1: number, z1: number, visit: (cx: number, cz: number, t0: number, t1: number) => void) {
  const ts = [0, 1];
  for (const [a, b] of [[x0, x1], [z0, z1]]) {
    if (a === b) continue;
    const lo = Math.min(a, b), hi = Math.max(a, b);
    for (let g = Math.ceil(lo / CELL_M) * CELL_M; g < hi; g += CELL_M) if (g > lo) ts.push((g - a) / (b - a));
  }
  ts.sort((p, q) => p - q);
  for (let i = 0; i + 1 < ts.length; i++) {
    const m = (ts[i] + ts[i + 1]) / 2;
    visit(Math.floor((x0 + (x1 - x0) * m) / CELL_M), Math.floor((z0 + (z1 - z0) * m) / CELL_M), ts[i], ts[i + 1]);
  }
}

/** Point and unit tangent at arc length s along a chain. */
export function pointAt(chain: Chain, s: number, out: { x: number; z: number; dx: number; dz: number; y: number }) {
  const c = chain.cumulative, p = chain.points;
  const clamped = Math.min(Math.max(s, 0), chain.length);
  let lo = 0, hi = c.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (c[mid] <= clamped) lo = mid; else hi = mid; }
  const span = c[hi] - c[lo], t = span > 0 ? (clamped - c[lo]) / span : 0;
  const dx = p[2 * hi] - p[2 * lo], dz = p[2 * hi + 1] - p[2 * lo + 1], len = Math.sqrt(dx * dx + dz * dz) || 1;
  out.x = p[2 * lo] + dx * t; out.z = p[2 * lo + 1] + dz * t; out.dx = dx / len; out.dz = dz / len;
  out.y = chain.heights ? chain.heights[lo] + (chain.heights[hi] - chain.heights[lo]) * t : NaN;
  return out;
}
