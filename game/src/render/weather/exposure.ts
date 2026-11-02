import * as THREE from 'three/webgpu';
import type { RainMeshData } from './surfaceIndex';
const SIZE = 32, CELL = 2, EXTENT = SIZE * CELL;
/** Geometry is copied once at stream-in. All triangle indexing and roof/ground queries run off the render thread. */
export class RainExposure {
  readonly texture: THREE.DataTexture;
  readonly origin = new THREE.Vector2(-32, -32);
  private readonly worker = new Worker(new URL('./exposure.worker.ts', import.meta.url), { type: 'module' });
  private readonly models = new Set<string>();
  private dirty = true;
  private sequence = 0;
  private requested = -1;
  private originX = Infinity;
  private originZ = Infinity;
  private lastQuery = -Infinity;
  private disposed = false;
  pending = false;
  constructor() {
    const initial = new Float32Array(SIZE * SIZE * 2);
    for (let i = 0; i < initial.length; i += 2) initial[i] = 65536;
    this.texture = new THREE.DataTexture(initial, SIZE, SIZE, THREE.RGFormat, THREE.FloatType);
    this.texture.magFilter = this.texture.minFilter = THREE.NearestFilter;
    this.texture.generateMipmaps = false; this.texture.needsUpdate = true;
    this.worker.onmessage = event => {
      if (this.disposed) return;
      if (event.data.kind === 'changed') { this.dirty = true; return; }
      if (event.data.sequence !== this.requested) return;
      this.pending = false;
      this.texture.image.data = event.data.heights;
      this.origin.set(event.data.x, event.data.z);
      this.texture.needsUpdate = true;
    };
  }
  add(model: THREE.Object3D) {
    if (model.userData.scenery || this.models.has(model.uuid)) return;
    this.models.add(model.uuid); model.updateWorldMatrix(true, true);
    const meshes: RainMeshData[] = [], transfers: ArrayBuffer[] = [];
    model.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh || (mesh as THREE.InstancedMesh).isInstancedMesh) return;
      const position = mesh.geometry.getAttribute('position') as THREE.BufferAttribute | THREE.InterleavedBufferAttribute;
      if (!position) return;
      const interleaved = (position as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute;
      const buffer = interleaved ? (position as THREE.InterleavedBufferAttribute).data.array : position.array;
      const copy = buffer.slice();
      const index = mesh.geometry.index?.array.slice() as RainMeshData['index'];
      meshes.push({ position: copy, stride: interleaved ? (position as THREE.InterleavedBufferAttribute).data.stride : position.itemSize,
        offset: interleaved ? (position as THREE.InterleavedBufferAttribute).offset : 0, normalized: position.normalized,
        index, matrix: mesh.matrixWorld.toArray() });
      transfers.push(copy.buffer as ArrayBuffer); if (index) transfers.push(index.buffer as ArrayBuffer);
    });
    this.worker.postMessage({ kind: 'add', id: model.uuid, meshes }, transfers);
  }
  remove(model: THREE.Object3D) {
    if (!this.models.delete(model.uuid)) return;
    this.worker.postMessage({ kind: 'remove', id: model.uuid });
  }
  update(camera: THREE.Camera) {
    const x = Math.floor(camera.position.x / CELL) * CELL - EXTENT / 2;
    const z = Math.floor(camera.position.z / CELL) * CELL - EXTENT / 2;
    const now = performance.now();
    if (this.pending || now - this.lastQuery < 100 || (!this.dirty && x === this.originX && z === this.originZ)) return;
    this.originX = x; this.originZ = z; this.dirty = false; this.pending = true; this.lastQuery = now;
    this.requested = ++this.sequence;
    this.worker.postMessage({ kind: 'query', sequence: this.requested, x, z, size: SIZE, cell: CELL });
  }
  dispose() { this.disposed = true; this.worker.terminate(); this.texture.dispose(); this.models.clear(); }
}
