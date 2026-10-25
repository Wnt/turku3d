// Finds which triangle of a trimesh contains a ray hit point. Rapier 0.21's JS bindings report
// featureId = 0 for every trimesh face hit (verified in tests/unit/physics.test.ts history), so the per-triangle
// material lookup cannot rely on it. A uniform XZ grid (CELL m) over the tile-local triangles keeps lookups O(1).
const CELL = 2.0;
/** Triangles are binned with their XZ bounds padded by PAD, so a hit point that float error puts a hair
 *  outside the triangle's bounds (e.g. local x = -1e-5 at a tile edge, or across a 2 m cell line) still finds
 *  it. locate() tolerances above PAD are not guaranteed. */
const PAD = 0.05;

export class TriLocator {
  private cells = new Map<number, number[]>();
  private pos: Float32Array;
  private idx: Uint32Array;
  private origin: [number, number, number];
  constructor(pos: Float32Array, idx: Uint32Array, origin: [number, number, number]) {
    this.pos = pos; this.idx = idx; this.origin = origin;
    const n = idx.length / 3;
    for (let t = 0; t < n; t++) {
      let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
      for (let k = 0; k < 3; k++) {
        const v = idx[3 * t + k];
        x0 = Math.min(x0, pos[3 * v]); x1 = Math.max(x1, pos[3 * v]);
        z0 = Math.min(z0, pos[3 * v + 2]); z1 = Math.max(z1, pos[3 * v + 2]);
      }
      for (let cz = Math.floor((z0 - PAD) / CELL); cz <= Math.floor((z1 + PAD) / CELL); cz++)
        for (let cx = Math.floor((x0 - PAD) / CELL); cx <= Math.floor((x1 + PAD) / CELL); cx++) {
          const key = this.key(cx, cz);
          let a = this.cells.get(key); if (!a) this.cells.set(key, (a = [])); a.push(t);
        }
    }
  }
  private key(cx: number, cz: number) { return (cx + 32768) * 65536 + (cz + 32768); }

  /** Index (into the trimesh's triangles) of the triangle nearest to world point p, or -1. */
  locate(px: number, py: number, pz: number, tol = 0.01): number {
    const x = px - this.origin[0], y = py - this.origin[1], z = pz - this.origin[2];
    const list = this.cells.get(this.key(Math.floor(x / CELL), Math.floor(z / CELL)));
    if (!list) return -1;
    let best = -1, bestD = tol;
    for (const t of list) {
      const d = this.distToTri(t, x, y, z);
      if (d < bestD) { bestD = d; best = t; }
    }
    return best;
  }

  /** Highest vertex of the contacted face, in world-local metres. */
  top(triangle: number): number {
    return this.origin[1] + Math.max(...[0, 1, 2].map(k => this.pos[3 * this.idx[3 * triangle + k] + 1]));
  }

  private distToTri(t: number, px: number, py: number, pz: number): number {
    const P = this.pos, I = this.idx;
    const a = 3 * I[3 * t], b = 3 * I[3 * t + 1], c = 3 * I[3 * t + 2];
    // closest point on triangle (Ericson, Real-Time Collision Detection 5.1.5)
    const abx = P[b] - P[a], aby = P[b + 1] - P[a + 1], abz = P[b + 2] - P[a + 2];
    const acx = P[c] - P[a], acy = P[c + 1] - P[a + 1], acz = P[c + 2] - P[a + 2];
    const apx = px - P[a], apy = py - P[a + 1], apz = pz - P[a + 2];
    const d1 = abx * apx + aby * apy + abz * apz, d2 = acx * apx + acy * apy + acz * apz;
    let qx: number, qy: number, qz: number;
    const bpx = px - P[b], bpy = py - P[b + 1], bpz = pz - P[b + 2];
    const d3 = abx * bpx + aby * bpy + abz * bpz, d4 = acx * bpx + acy * bpy + acz * bpz;
    const cpx = px - P[c], cpy = py - P[c + 1], cpz = pz - P[c + 2];
    const d5 = abx * cpx + aby * cpy + abz * cpz, d6 = acx * cpx + acy * cpy + acz * cpz;
    const vc = d1 * d4 - d3 * d2, vb = d5 * d2 - d1 * d6, va = d3 * d6 - d5 * d4;
    if (d1 <= 0 && d2 <= 0) { qx = P[a]; qy = P[a + 1]; qz = P[a + 2]; }
    else if (d3 >= 0 && d4 <= d3) { qx = P[b]; qy = P[b + 1]; qz = P[b + 2]; }
    else if (d6 >= 0 && d5 <= d6) { qx = P[c]; qy = P[c + 1]; qz = P[c + 2]; }
    else if (vc <= 0 && d1 >= 0 && d3 <= 0) { const v = d1 / (d1 - d3); qx = P[a] + v * abx; qy = P[a + 1] + v * aby; qz = P[a + 2] + v * abz; }
    else if (vb <= 0 && d2 >= 0 && d6 <= 0) { const w = d2 / (d2 - d6); qx = P[a] + w * acx; qy = P[a + 1] + w * acy; qz = P[a + 2] + w * acz; }
    else if (va <= 0 && d4 - d3 >= 0 && d5 - d6 >= 0) {
      const w = (d4 - d3) / (d4 - d3 + (d5 - d6));
      qx = P[b] + w * (P[c] - P[b]); qy = P[b + 1] + w * (P[c + 1] - P[b + 1]); qz = P[b + 2] + w * (P[c + 2] - P[b + 2]);
    } else {
      const den = 1 / (va + vb + vc), v = vb * den, w = vc * den;
      qx = P[a] + abx * v + acx * w; qy = P[a + 1] + aby * v + acy * w; qz = P[a + 2] + abz * v + acz * w;
    }
    return Math.hypot(px - qx, py - qy, pz - qz);
  }
}
