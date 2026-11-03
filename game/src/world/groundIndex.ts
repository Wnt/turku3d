// Ground heights from the decoded collision cells, for modes without the physics worker (fixed cameras, replays):
// ambient car wheels, lane fallbacks and walkers stand on the same surfaces as in play (physics raycasts onto
// collision), at the cost of a grid lookup. Raycasting the visual meshes instead cost most of a replay frame.
import { COLLISION_TILE, collisionTileAt, collisionTileId } from '../core/origin';
import type { ColTile } from '../physics/colTile';
import { MAT_FLAG, MATERIALS } from '../shared/col/materials';

/** Grid step inside a collision cell (m). */
const STEP = 2;
/** Surfaces a wheel or a foot stands on: solid and walkable, upward-facing; not roofs or street furniture. */
const GROUND = MATERIALS.map(m => m.cls === 'solid' && !(m.flags & MAT_FLAG.NOWALK) && m.name !== 'roof' && m.name !== 'street_furniture');
const MIN_UP = 0.5;

interface Cell {
  x0: number; z0: number; nx: number; nz: number;
  /** CSR lists: triangles overlapping grid square k are tris[start[k] .. start[k + 1]). */
  start: Uint32Array; tris: Uint32Array;
  /** World-space vertices, nine floats per kept triangle. */
  v: Float32Array;
  /** `generation` when this cell was indexed. */
  version: number;
}

export class GroundIndex {
  private cells = new Map<string, Cell>();
  /** Changes whenever a cell is added, replaced or removed (callers key caches by it). */
  generation = 0;

  get size() { return this.cells.size; }
  /** Retained bytes and cells (memory census). */
  retainedBytes() {
    let bytes = 0;
    for (const cell of this.cells.values()) bytes += cell.start.byteLength + cell.tris.byteLength + cell.v.byteLength;
    return { bytes, count: this.cells.size };
  }

  /** Index one decoded collision cell (replaces an earlier one with the same id). */
  add(col: ColTile) {
    const p = col.positions, idx = col.indices, [ox, oy, oz] = col.origin;
    const kept: number[] = [];
    for (let t = 0; t < idx.length / 3; t++) {
      if (!GROUND[col.materials[t]]) continue;
      const a = 3 * idx[3 * t], b = 3 * idx[3 * t + 1], c = 3 * idx[3 * t + 2];
      const ux = p[b] - p[a], uy = p[b + 1] - p[a + 1], uz = p[b + 2] - p[a + 2];
      const vx = p[c] - p[a], vy = p[c + 1] - p[a + 1], vz = p[c + 2] - p[a + 2];
      const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx, n = Math.hypot(nx, ny, nz);
      if (n > 1e-9 && Math.abs(ny) / n > MIN_UP) kept.push(t);
    }
    const v = new Float32Array(kept.length * 9);
    let x0 = Infinity, z0 = Infinity, x1 = -Infinity, z1 = -Infinity;
    kept.forEach((t, k) => {
      for (let j = 0; j < 3; j++) {
        const i = 3 * idx[3 * t + j];
        const x = p[i] + ox, z = p[i + 2] + oz;
        v[9 * k + 3 * j] = x; v[9 * k + 3 * j + 1] = p[i + 1] + oy; v[9 * k + 3 * j + 2] = z;
        x0 = Math.min(x0, x); x1 = Math.max(x1, x); z0 = Math.min(z0, z); z1 = Math.max(z1, z);
      }
    });
    if (!kept.length) { x0 = z0 = 0; x1 = z1 = STEP; }
    const nx = Math.max(1, Math.ceil((x1 - x0) / STEP)), nz = Math.max(1, Math.ceil((z1 - z0) / STEP));
    const count = new Uint32Array(nx * nz + 1);
    const span = (k: number, visit: (cell: number) => void) => {
      const ax = Math.min(v[9 * k], v[9 * k + 3], v[9 * k + 6]), bx = Math.max(v[9 * k], v[9 * k + 3], v[9 * k + 6]);
      const az = Math.min(v[9 * k + 2], v[9 * k + 5], v[9 * k + 8]), bz = Math.max(v[9 * k + 2], v[9 * k + 5], v[9 * k + 8]);
      const i0 = Math.max(0, Math.floor((ax - x0) / STEP)), i1 = Math.min(nx - 1, Math.floor((bx - x0) / STEP));
      const j0 = Math.max(0, Math.floor((az - z0) / STEP)), j1 = Math.min(nz - 1, Math.floor((bz - z0) / STEP));
      for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) visit(j * nx + i);
    };
    for (let k = 0; k < kept.length; k++) span(k, cell => count[cell + 1]++);
    for (let i = 1; i < count.length; i++) count[i] += count[i - 1];
    const start = count.slice(), fill = count.slice(0, -1), tris = new Uint32Array(count[count.length - 1]);
    for (let k = 0; k < kept.length; k++) span(k, cell => { tris[fill[cell]++] = k; });
    this.generation++;
    this.cells.set(col.id, { x0, z0, nx, nz, start, tris, v, version: this.generation });
  }

  /** Version of the collision cell under (x, z), or -1 when none is resident: a cached height is valid while it holds. */
  version(x: number, z: number): number {
    const { i, j } = collisionTileAt(x, z);
    return this.cells.get(collisionTileId(i, j))?.version ?? -1;
  }

  remove(id: string) { if (this.cells.delete(id)) this.generation++; }

  clear() { if (this.cells.size) { this.cells.clear(); this.generation++; } }

  /** The highest ground surface at (x, z) between `top` and `top - depth`, as a downward ray would find it; null
   *  when there is none or the collision there is not resident. */
  ground(x: number, top: number, z: number, depth: number): number | null {
    const { i, j } = collisionTileAt(x, z);
    let best = this.cellGround(this.cells.get(collisionTileId(i, j)), x, top, z, depth);
    // A triangle on a cell seam may live in the neighbour only.
    const fx = x / COLLISION_TILE - i, fz = z / COLLISION_TILE - j, near = 0.5 / COLLISION_TILE;
    for (const [di, dj] of [[-1, 0], [1, 0], [0, -1], [0, 1]]) {
      if (di < 0 && fx > near || di > 0 && fx < 1 - near || dj < 0 && fz > near || dj > 0 && fz < 1 - near) continue;
      const y = this.cellGround(this.cells.get(collisionTileId(i + di, j + dj)), x, top, z, depth);
      if (y !== null && (best === null || y > best)) best = y;
    }
    return best;
  }

  private cellGround(cell: Cell | undefined, x: number, top: number, z: number, depth: number): number | null {
    if (!cell) return null;
    const gi = Math.floor((x - cell.x0) / STEP), gj = Math.floor((z - cell.z0) / STEP);
    if (gi < 0 || gj < 0 || gi >= cell.nx || gj >= cell.nz) return null;
    const k = gj * cell.nx + gi, v = cell.v, low = top - depth;
    let best: number | null = null;
    for (let n = cell.start[k]; n < cell.start[k + 1]; n++) {
      const o = 9 * cell.tris[n];
      const ax = v[o], az = v[o + 2], bx = v[o + 3], bz = v[o + 5], cx = v[o + 6], cz = v[o + 8];
      const d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz);
      if (Math.abs(d) < 1e-12) continue;
      const w0 = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / d, w1 = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / d, w2 = 1 - w0 - w1;
      if (w0 < -1e-6 || w1 < -1e-6 || w2 < -1e-6) continue;
      const y = w0 * v[o + 1] + w1 * v[o + 4] + w2 * v[o + 7];
      if (y <= top + 1e-4 && y >= low && (best === null || y > best)) best = y;
    }
    return best;
  }
}
