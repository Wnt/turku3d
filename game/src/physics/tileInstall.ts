// Collision cell installs in pieces small enough to build between physics steps.
//
// A Rapier trimesh collider is built in one synchronous WASM call (BVH plus, with FIX_INTERNAL_EDGES, the edge
// topology): about 1 µs per triangle, so a dense 30-40k-triangle cell blocked the physics worker for 20-55 ms in
// Node and longer in the browser, and four of them queued back to back held the car's pose for 200 ms
// (capture 20261103-201621-v8hjwo). `planCell` splits each collision class of a decoded cell into spatially
// compact meshes of at most MAX_PIECE_TRIS triangles with their own compacted vertices; `InstallQueue` builds
// them a few at a time under a per-tick time budget, nearest cell first, and a cell joins the world only when all
// its pieces exist.
import type { ColTile } from './colTile';
import { materialClass, type CollisionClass } from '../shared/col/materials';

/** Triangles per trimesh piece: about 1 ms to build in Rapier 0.21 on an idle M1 Max (2-5 ms with the machine loaded). */
export const MAX_PIECE_TRIS = 1000;
/** Primitive colliders built per piece (each a cheap cuboid/capsule/cylinder). */
const PRIMS_PER_PIECE = 48;

export type { CollisionClass };
const CLASSES = ['solid', 'query', 'sensor'] as const;
/** Collision class index per material id (0 solid, 1 query, 2 sensor). */
const CLASS_CODE = Uint8Array.from({ length: 256 }, (_, id) => CLASSES.indexOf(materialClass(id)));
/** A trimesh piece: its triangles (indices into the cell's), compacted to its own vertices when built. */
export interface MeshRef { kind: 'mesh'; cls: CollisionClass; tris: Uint32Array }
export interface PrimPiece { kind: 'prims'; from: number; to: number }
export type Piece = MeshRef | PrimPiece;
export interface MeshPiece { cls: CollisionClass; positions: Float32Array; indices: Uint32Array; materials: Uint8Array }
export interface CellPlan { pieces: Piece[]; tris: number; lo: number; hi: number }

/** Splits a decoded cell into install pieces: each collision class into spatially compact sets of at most
 *  `maxTris` triangles (in-place halving of the centroids' longer XZ extent), then the primitives in batches.
 *  Vertices are compacted per piece when it is built (compactPiece). */
export function planCell(col: ColTile, maxTris = MAX_PIECE_TRIS): CellPlan {
  const planner = new CellPlanner(col, maxTris);
  while (!planner.advance(Infinity));
  return planner.plan!;
}

/** planCell in bounded steps: `advance(work)` visits about `work` triangles (a few ms per 100k) and returns true
 *  once `plan` is ready, so a dense cell's split never holds a physics step either. */
export class CellPlanner {
  plan: CellPlan | null = null;
  private cx: Float32Array | null = null;
  private cz: Float32Array | null = null;
  private order: Uint32Array | null = null;
  private stack: [number, number, number][] = [];
  private pieces: Piece[] = [];
  constructor(private readonly col: ColTile, private readonly maxTris = MAX_PIECE_TRIS) {}

  advance(work: number): boolean {
    if (this.plan) return true;
    const col = this.col, P = col.positions, I = col.indices, M = col.materials, n = M.length;
    if (!this.order) {
      // Centroids and the triangles grouped by class (solid, query, sensor) in one array, split in place below.
      const cx = this.cx = new Float32Array(n), cz = this.cz = new Float32Array(n);
      for (let t = 0; t < n; t++) {
        const a = 3 * I[3 * t], b = 3 * I[3 * t + 1], c = 3 * I[3 * t + 2];
        cx[t] = (P[a] + P[b] + P[c]) / 3; cz[t] = (P[a + 2] + P[b + 2] + P[c + 2]) / 3;
      }
      const order = this.order = new Uint32Array(n), counts = [0, 0, 0];
      for (let t = 0; t < n; t++) counts[CLASS_CODE[M[t]]]++;
      const start = [0, counts[0], counts[0] + counts[1]], fill = start.slice();
      for (let t = 0; t < n; t++) order[fill[CLASS_CODE[M[t]]]++] = t;
      for (let code = 2; code >= 0; code--) if (counts[code]) this.stack.push([start[code], start[code] + counts[code], code]);
      work -= 3 * n;
      if (work <= 0) return false;
    }
    const cx = this.cx!, cz = this.cz!, order = this.order, stack = this.stack, maxTris = this.maxTris;
    while (stack.length) {
      if (work <= 0) return false;
      const [lo, hi, code] = stack.pop()!;
      if (hi - lo > maxTris) {
        work -= 2 * (hi - lo);
        let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
        for (let k = lo; k < hi; k++) {
          const x = cx[order[k]], z = cz[order[k]];
          if (x < x0) x0 = x; if (x > x1) x1 = x; if (z < z0) z0 = z; if (z > z1) z1 = z;
        }
        const alongX = x1 - x0 >= z1 - z0, extent = alongX ? x1 - x0 : z1 - z0;
        if (extent > 0.05) {
          const mid = alongX ? (x0 + x1) / 2 : (z0 + z1) / 2, key = alongX ? cx : cz;
          let i = lo, j = hi - 1;
          while (i <= j) {
            if (key[order[i]] < mid) i++;
            else { const t = order[i]; order[i] = order[j]; order[j] = t; j--; }
          }
          if (i > lo && i < hi) { stack.push([i, hi, code], [lo, i, code]); continue; }
        }
      }
      this.pieces.push({ kind: 'mesh', cls: CLASSES[code], tris: order.subarray(lo, hi) });
    }
    for (let from = 0; from < col.prims.length; from += PRIMS_PER_PIECE)
      this.pieces.push({ kind: 'prims', from, to: Math.min(col.prims.length, from + PRIMS_PER_PIECE) });
    const [lo, hi] = heightRange(col);
    this.plan = { pieces: this.pieces, tris: n, lo, hi };
    this.cx = this.cz = null;
    return true;
  }
}

/** One piece's triangles with only the vertices they use (Rapier copies every vertex it is given). `remap` is a
 *  scratch array of the cell's vertex count filled with -1, and is left so. */
export function compactPiece(col: ColTile, piece: MeshRef, remap: Int32Array): MeshPiece {
  const P = col.positions, I = col.indices, M = col.materials, tris = piece.tris;
  const indices = new Uint32Array(tris.length * 3), materials = new Uint8Array(tris.length);
  const used = new Uint32Array(tris.length * 3);
  let count = 0;
  for (let k = 0; k < tris.length; k++) {
    const t = tris[k];
    materials[k] = M[t];
    for (let e = 0; e < 3; e++) {
      const v = I[3 * t + e];
      let m = remap[v];
      if (m < 0) { m = remap[v] = count; used[count++] = v; }
      indices[3 * k + e] = m;
    }
  }
  const positions = new Float32Array(count * 3);
  for (let k = 0; k < count; k++) {
    const v = used[k];
    positions[3 * k] = P[3 * v]; positions[3 * k + 1] = P[3 * v + 1]; positions[3 * k + 2] = P[3 * v + 2];
    remap[v] = -1;
  }
  return { cls: piece.cls, positions, indices, materials };
}

/** World heights spanned by a tile's triangles and primitives. */
export function heightRange(col: ColTile): [number, number] {
  let lo = Infinity, hi = -Infinity;
  const P = col.positions;
  for (let k = 1; k < P.length; k += 3) { const y = P[k]; if (y < lo) lo = y; if (y > hi) hi = y; }
  for (const p of col.prims) { lo = Math.min(lo, p.center[1]); hi = Math.max(hi, p.center[1]); }
  return Number.isFinite(lo) ? [lo + col.origin[1], hi + col.origin[1]] : [col.origin[1], col.origin[1]];
}

/** One unit of queued worker work: `run` does at most one bounded piece and reports whether the job is finished;
 *  `cancel` undoes partial work. Lower `priority()` runs first. */
export interface InstallJob {
  key: string;
  /** What the latest run did (stall reports). */
  phase?: string;
  priority(): number;
  run(): boolean;
  cancel(): void;
}

/** Jobs run nearest first under a time budget per physics tick; at least one piece always runs, so a cell
 *  completes even under a budget smaller than its largest piece. */
export class InstallQueue {
  private jobs = new Map<string, InstallJob>();
  /** Longest single piece of the latest pump (ms) and what it was, for stall reports. */
  slowest = { ms: 0, key: '', phase: '' };
  get size() { return this.jobs.size; }
  has(key: string) { return this.jobs.has(key); }
  /** Adds a job, replacing (and cancelling) a queued one with the same key. */
  add(job: InstallJob) { this.cancel(job.key); this.jobs.set(job.key, job); }
  cancel(key: string): boolean {
    const job = this.jobs.get(key);
    if (!job) return false;
    this.jobs.delete(key); job.cancel();
    return true;
  }
  /** Runs pieces until `budgetMs` is spent. Returns the time used (ms). */
  pump(budgetMs: number, now: () => number = () => performance.now()): number {
    const t0 = now();
    this.slowest = { ms: 0, key: '', phase: '' };
    let job: InstallJob | null = null;
    for (;;) {
      if (!this.jobs.size) break;
      const elapsed = now() - t0;
      if (job && elapsed >= budgetMs) break;
      job = null;
      let best = Infinity;
      for (const candidate of this.jobs.values()) { const p = candidate.priority(); if (p < best || !job) { best = p; job = candidate; } }
      const current = job!, p0 = now();
      let done = true;
      try { done = current.run(); } finally {
        const ms = now() - p0;
        if (ms > this.slowest.ms) this.slowest = { ms, key: current.key, phase: current.phase ?? '' };
        if (done && this.jobs.get(current.key) === current) this.jobs.delete(current.key);
      }
    }
    return now() - t0;
  }
}
