/** Worker-side XZ acceleration for downward rain exposure. Only projected triangles occupy buckets. */
export interface RainMeshData {
  position: ArrayBufferView; stride: number; offset: number; normalized: boolean;
  index: Uint16Array | Uint32Array | Uint8Array | null; matrix: number[];
}
const BUCKET = 8;
function component(array: ArrayBufferView, index: number, normalized: boolean) {
  const values = array as unknown as ArrayLike<number>;
  const value = values[index];
  if (!normalized || array instanceof Float32Array || array instanceof Float64Array) return value;
  if (array instanceof Int16Array) return Math.max(value / 32767, -1);
  if (array instanceof Uint16Array) return value / 65535;
  if (array instanceof Int8Array) return Math.max(value / 127, -1);
  if (array instanceof Uint8Array) return value / 255;
  return value;
}
interface Triangle { x: number; z: number; y: number; bx: number; bz: number; by: number; cx: number; cz: number; cy: number; inverse: number; flat: boolean }
export class RainSurfaceIndex {
  private readonly buckets = new Map<string, Triangle[]>();
  constructor(meshes: RainMeshData[]) {
    for (const mesh of meshes) {
      const m = mesh.matrix, count = mesh.position.byteLength / (mesh.position as Float32Array).BYTES_PER_ELEMENT / mesh.stride;
      const read = (index: number) => {
        const offset = index * mesh.stride + mesh.offset;
        const x = component(mesh.position, offset, mesh.normalized), y = component(mesh.position, offset + 1, mesh.normalized), z = component(mesh.position, offset + 2, mesh.normalized);
        return [m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13], m[2] * x + m[6] * y + m[10] * z + m[14]];
      };
      const vertices = mesh.index?.length ?? count;
      for (let i = 0; i + 2 < vertices; i += 3) {
        const a = read(mesh.index?.[i] ?? i), b = read(mesh.index?.[i + 1] ?? i + 1), c = read(mesh.index?.[i + 2] ?? i + 2);
        const bx = b[0] - a[0], bz = b[2] - a[2], cx = c[0] - a[0], cz = c[2] - a[2], by = b[1] - a[1], cy = c[1] - a[1];
        const determinant = bx * cz - bz * cx;
        if (Math.abs(determinant) < 1e-7) continue;
        const nx = by * cz - bz * cy, nz = bx * cy - by * cx;
        const flat = Math.abs(determinant) / Math.hypot(nx, determinant, nz) > .85;
        const triangle = { x: a[0], z: a[2], y: a[1], bx, bz, by, cx, cz, cy, inverse: 1 / determinant, flat };
        const x0 = Math.floor(Math.min(a[0], b[0], c[0]) / BUCKET), x1 = Math.floor(Math.max(a[0], b[0], c[0]) / BUCKET);
        const z0 = Math.floor(Math.min(a[2], b[2], c[2]) / BUCKET), z1 = Math.floor(Math.max(a[2], b[2], c[2]) / BUCKET);
        for (let z = z0; z <= z1; z++) for (let x = x0; x <= x1; x++) {
          const key = `${x},${z}`; let bucket = this.buckets.get(key);
          if (!bucket) this.buckets.set(key, bucket = []);
          bucket.push(triangle);
        }
      }
    }
  }
  height(x: number, z: number): { y: number; flat: boolean } | null {
    const bucket = this.buckets.get(`${Math.floor(x / BUCKET)},${Math.floor(z / BUCKET)}`);
    if (!bucket) return null;
    let y = -Infinity, flat = false;
    for (const t of bucket) {
      const px = x - t.x, pz = z - t.z;
      const u = (px * t.cz - pz * t.cx) * t.inverse, v = (t.bx * pz - t.bz * px) * t.inverse;
      if (u < -1e-5 || v < -1e-5 || u + v > 1.00001) continue;
      const h = t.y + u * t.by + v * t.cy;
      if (h > y) { y = h; flat = t.flat; }
    }
    return y > -Infinity ? { y, flat } : null;
  }
}

/** Bounded spill depth in metres. Boundary cells drain; a connected gutter stays open rather than pooling. */
export function rainBasins(heights: Float32Array, size: number) {
  const visited = new Uint8Array(size * size), heap: { index: number; level: number }[] = [];
  const push = (index: number, level: number) => {
    const item = { index, level }; let at = heap.length; heap.push(item);
    while (at > 0) { const parent = (at - 1) >>> 1; if (heap[parent].level <= level) break; heap[at] = heap[parent]; at = parent; }
    heap[at] = item;
  };
  const pop = () => {
    const first = heap[0], last = heap.pop()!;
    if (heap.length) {
      let at = 0;
      while (at * 2 + 1 < heap.length) {
        let child = at * 2 + 1;
        if (child + 1 < heap.length && heap[child + 1].level < heap[child].level) child++;
        if (heap[child].level >= last.level) break;
        heap[at] = heap[child]; at = child;
      }
      heap[at] = last;
    }
    return first;
  };
  for (let j = 0; j < size; j++) for (let i = 0; i < size; i++) {
    const index = j * size + i; heights[index * 4 + 2] = 0;
    if (i !== 0 && j !== 0 && i !== size - 1 && j !== size - 1) continue;
    visited[index] = 1; push(index, heights[index * 4]);
  }
  while (heap.length) {
    const { index, level } = pop(), x = index % size, z = Math.floor(index / size);
    for (const next of [x > 0 ? index - 1 : -1, x + 1 < size ? index + 1 : -1, z > 0 ? index - size : -1, z + 1 < size ? index + size : -1]) {
      if (next < 0 || visited[next]) continue;
      visited[next] = 1;
      const elevation = heights[next * 4], spill = Math.max(elevation, level);
      if (heights[next * 4 + 1]) heights[next * 4 + 2] = Math.min(.12, spill - elevation);
      push(next, spill);
    }
  }
  return heights;
}
