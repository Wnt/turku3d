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
/** Triangle record: anchor vertex a (x, z, y), edges b - a and c - a (x, z, y), 1 / xz determinant. */
const FIELDS = 10;
/** Bucket keys stay small integers (no strings, no boxed numbers): +-16k buckets of 8 m = +-131 km around the origin. */
const KEY_SPAN = 32768, KEY_BIAS = 16384;
const bucketKey = (x: number, z: number) => (x + KEY_BIAS) * KEY_SPAN + (z + KEY_BIAS);
/** Typed storage: one tile held ~a million projected triangles. As one JS object per triangle (plus string bucket keys)
 *  the exposure worker's heap reached 444 MB within two minutes of city.lab play and its major GCs paused it 220-740 ms
 *  (framecheck mac-heap-1, M1 Max); flat arrays hold the same triangles without anything for the GC to trace. */
export class RainSurfaceIndex {
  private readonly triangles: Float64Array;
  private readonly flat: Uint8Array;
  private readonly buckets = new Map<number, Int32Array>();
  constructor(meshes: RainMeshData[]) {
    let capacity = 0;
    for (const mesh of meshes) capacity += Math.floor((mesh.index?.length ?? mesh.position.byteLength / (mesh.position as Float32Array).BYTES_PER_ELEMENT / mesh.stride) / 3);
    const triangles = new Float64Array(capacity * FIELDS), flat = new Uint8Array(capacity);
    const members = new Map<number, number[]>();
    const v = new Float64Array(9);
    let count = 0;
    for (const mesh of meshes) {
      const m = mesh.matrix, vertexCount = mesh.position.byteLength / (mesh.position as Float32Array).BYTES_PER_ELEMENT / mesh.stride;
      const read = (index: number, at: number) => {
        const offset = index * mesh.stride + mesh.offset;
        const x = component(mesh.position, offset, mesh.normalized), y = component(mesh.position, offset + 1, mesh.normalized), z = component(mesh.position, offset + 2, mesh.normalized);
        v[at] = m[0] * x + m[4] * y + m[8] * z + m[12]; v[at + 1] = m[1] * x + m[5] * y + m[9] * z + m[13]; v[at + 2] = m[2] * x + m[6] * y + m[10] * z + m[14];
      };
      const vertices = mesh.index?.length ?? vertexCount;
      for (let i = 0; i + 2 < vertices; i += 3) {
        read(mesh.index?.[i] ?? i, 0); read(mesh.index?.[i + 1] ?? i + 1, 3); read(mesh.index?.[i + 2] ?? i + 2, 6);
        const bx = v[3] - v[0], bz = v[5] - v[2], cx = v[6] - v[0], cz = v[8] - v[2], by = v[4] - v[1], cy = v[7] - v[1];
        const determinant = bx * cz - bz * cx;
        if (Math.abs(determinant) < 1e-7) continue;
        const nx = by * cz - bz * cy, nz = bx * cy - by * cx;
        const t = count * FIELDS;
        triangles[t] = v[0]; triangles[t + 1] = v[2]; triangles[t + 2] = v[1];
        triangles[t + 3] = bx; triangles[t + 4] = bz; triangles[t + 5] = by;
        triangles[t + 6] = cx; triangles[t + 7] = cz; triangles[t + 8] = cy; triangles[t + 9] = 1 / determinant;
        flat[count] = Math.abs(determinant) / Math.hypot(nx, determinant, nz) > .85 ? 1 : 0;
        const x0 = Math.floor(Math.min(v[0], v[3], v[6]) / BUCKET), x1 = Math.floor(Math.max(v[0], v[3], v[6]) / BUCKET);
        const z0 = Math.floor(Math.min(v[2], v[5], v[8]) / BUCKET), z1 = Math.floor(Math.max(v[2], v[5], v[8]) / BUCKET);
        for (let z = z0; z <= z1; z++) for (let x = x0; x <= x1; x++) {
          const key = bucketKey(x, z); let list = members.get(key);
          if (!list) members.set(key, list = []);
          list.push(count);
        }
        count++;
      }
    }
    this.triangles = triangles.slice(0, count * FIELDS); this.flat = flat.slice(0, count);
    for (const [key, list] of members) this.buckets.set(key, Int32Array.from(list));
  }
  height(x: number, z: number): { y: number; flat: boolean } | null {
    const bucket = this.buckets.get(bucketKey(Math.floor(x / BUCKET), Math.floor(z / BUCKET)));
    if (!bucket) return null;
    const t = this.triangles;
    let y = -Infinity, flat = false;
    for (let k = 0; k < bucket.length; k++) {
      const i = bucket[k], o = i * FIELDS;
      const px = x - t[o], pz = z - t[o + 1], inverse = t[o + 9];
      const u = (px * t[o + 7] - pz * t[o + 6]) * inverse, v = (t[o + 3] * pz - t[o + 4] * px) * inverse;
      if (u < -1e-5 || v < -1e-5 || u + v > 1.00001) continue;
      const h = t[o + 2] + u * t[o + 5] + v * t[o + 8];
      if (h > y) { y = h; flat = this.flat[i] === 1; }
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
