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

/** Triangles per trimesh piece: about 1-2 ms to build in Rapier 0.21 (M1 Max, Node and Chrome). */
export const MAX_PIECE_TRIS = 1500;
/** Primitive colliders built per piece (each a cheap cuboid/capsule/cylinder). */
const PRIMS_PER_PIECE = 48;

export type { CollisionClass };
export interface MeshPiece { kind: 'mesh'; cls: CollisionClass; positions: Float32Array; indices: Uint32Array; materials: Uint8Array }
export interface PrimPiece { kind: 'prims'; from: number; to: number }
export type Piece = MeshPiece | PrimPiece;
export interface CellPlan { pieces: Piece[]; tris: number; lo: number; hi: number }

/** Splits a decoded cell into install pieces: each collision class into compact meshes of at most `maxTris`
 *  triangles (recursive halving of the centroids' longer XZ extent), then the primitives in batches. */
export function planCell(col: ColTile, maxTris = MAX_PIECE_TRIS): CellPlan {
  const P = col.positions, I = col.indices, M = col.materials;
  const n = M.length;
  const cx = new Float32Array(n), cz = new Float32Array(n);
  for (let t = 0; t < n; t++) {
    const a = 3 * I[3 * t], b = 3 * I[3 * t + 1], c = 3 * I[3 * t + 2];
    cx[t] = (P[a] + P[b] + P[c]) / 3; cz[t] = (P[a + 2] + P[b + 2] + P[c + 2]) / 3;
  }
  const byClass: Record<CollisionClass, number[]> = { solid: [], query: [], sensor: [] };
  for (let t = 0; t < n; t++) byClass[materialClass(M[t])].push(t);
  const pieces: Piece[] = [];
  const remap = new Int32Array(P.length / 3).fill(-1);
  for (const cls of ['solid', 'query', 'sensor'] as const) {
    const all = Uint32Array.from(byClass[cls]);
    if (!all.length) continue;
    const stack: Uint32Array[] = [all];
    while (stack.length) {
      const tris = stack.pop()!;
      if (tris.length > maxTris) {
        let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
        for (const t of tris) { x0 = Math.min(x0, cx[t]); x1 = Math.max(x1, cx[t]); z0 = Math.min(z0, cz[t]); z1 = Math.max(z1, cz[t]); }
        const alongX = x1 - x0 >= z1 - z0, extent = alongX ? x1 - x0 : z1 - z0;
        if (extent > 0.05) {
          const mid = alongX ? (x0 + x1) / 2 : (z0 + z1) / 2, key = alongX ? cx : cz;
          let low = 0;
          for (const t of tris) if (key[t] < mid) low++;
          const a = new Uint32Array(low), b = new Uint32Array(tris.length - low);
          let ia = 0, ib = 0;
          for (const t of tris) if (key[t] < mid) a[ia++] = t; else b[ib++] = t;
          if (a.length && b.length) { stack.push(b, a); continue; }
        }
      }
      pieces.push(compact(cls, tris, P, I, M, remap));
    }
  }
  for (let from = 0; from < col.prims.length; from += PRIMS_PER_PIECE)
    pieces.push({ kind: 'prims', from, to: Math.min(col.prims.length, from + PRIMS_PER_PIECE) });
  const [lo, hi] = heightRange(col);
  return { pieces, tris: n, lo, hi };
}

/** One piece's triangles with only the vertices they use (Rapier copies every vertex it is given). */
function compact(cls: CollisionClass, tris: Uint32Array, P: Float32Array, I: Uint32Array, M: Uint8Array, remap: Int32Array): MeshPiece {
  const indices = new Uint32Array(tris.length * 3), materials = new Uint8Array(tris.length);
  const used: number[] = [];
  for (let k = 0; k < tris.length; k++) {
    const t = tris[k];
    materials[k] = M[t];
    for (let e = 0; e < 3; e++) {
      const v = I[3 * t + e];
      let m = remap[v];
      if (m < 0) { m = remap[v] = used.length; used.push(v); }
      indices[3 * k + e] = m;
    }
  }
  const positions = new Float32Array(used.length * 3);
  for (let k = 0; k < used.length; k++) {
    const v = used[k];
    positions[3 * k] = P[3 * v]; positions[3 * k + 1] = P[3 * v + 1]; positions[3 * k + 2] = P[3 * v + 2];
    remap[v] = -1;
  }
  return { kind: 'mesh', cls, positions, indices, materials };
}

/** World heights spanned by a tile's triangles and primitives. */
export function heightRange(col: ColTile): [number, number] {
  let lo = Infinity, hi = -Infinity;
  for (let k = 1; k < col.positions.length; k += 3) { lo = Math.min(lo, col.positions[k]); hi = Math.max(hi, col.positions[k]); }
  for (const p of col.prims) { lo = Math.min(lo, p.center[1]); hi = Math.max(hi, p.center[1]); }
  return Number.isFinite(lo) ? [lo + col.origin[1], hi + col.origin[1]] : [col.origin[1], col.origin[1]];
}

/** One unit of queued worker work: `run` does at most one bounded piece and reports whether the job is finished;
 *  `cancel` undoes partial work. Lower `priority()` runs first. */
export interface InstallJob {
  key: string;
  priority(): number;
  run(): boolean;
  cancel(): void;
}

/** Jobs run nearest first under a time budget per physics tick; at least one piece always runs, so a cell
 *  completes even under a budget smaller than its largest piece. */
export class InstallQueue {
  private jobs = new Map<string, InstallJob>();
  /** Longest single piece of the latest pump (ms) and what it was, for stall reports. */
  slowest = { ms: 0, key: '' };
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
    this.slowest = { ms: 0, key: '' };
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
        if (ms > this.slowest.ms) this.slowest = { ms, key: current.key };
        if (done && this.jobs.get(current.key) === current) this.jobs.delete(current.key);
      }
    }
    return now() - t0;
  }
}
