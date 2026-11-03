// GPU-resident bytes of scene content, for the residency budget (CoarseTiles) and the memory census. With the
// renderer's record (rendererResidency) only what is on the GPU counts: a pooled vertex/index buffer its slot
// (render/geometryPool.ts, no placement padding), any other buffer its GPU size rounded up to 64 KiB as D3D12 places
// it, a texture its mip data once it has a GPU texture; content not uploaded yet (hidden merge sources, LOD
// children never drawn) counts nothing. Without it, count x stride x element size per buffer, rounded the same way.
// Shared buffers and texture sources count once per measurement. three's info.memory is not used: it over-counts.
import type * as THREE from 'three/webgpu';
import { pooledGeometryBytes } from '../render/geometryPool';

const PLACEMENT = 64 * 1024;
const placed = (bytes: number) => bytes > 0 ? Math.ceil(bytes / PLACEMENT) * PLACEMENT : 0;

type Target = THREE.BufferAttribute | THREE.InterleavedBuffer;
/** What the renderer holds on the GPU for a buffer target (bytes) and whether a texture has a GPU texture. */
export interface GpuResidency { buffer(target: object): number; texture(texture: THREE.Texture): boolean }
/** The r186 WebGPU backend's own records (never creating entries); undefined without a backend (tests, workers). */
export function rendererResidency(renderer: unknown): GpuResidency | undefined {
  const data = (renderer as { backend?: { data?: WeakMap<object, { buffer?: { size?: number }; texture?: unknown }> } } | null)?.backend?.data;
  if (!data) return undefined;
  return {
    buffer: target => pooledGeometryBytes(target) ?? placed(data.get(target)?.buffer?.size ?? 0),
    texture: texture => !!data.get(texture)?.texture,
  };
}
function targetBytes(target: Target) {
  // Sub-allocated from a shared page (render/geometryPool.ts): its slot, not a 64 KiB placement. The slots of a page
  // add up to the page once.
  const pooled = pooledGeometryBytes(target);
  if (pooled !== undefined) return pooled;
  const array = target.array as unknown as { constructor: { BYTES_PER_ELEMENT?: number } };
  const element = array?.constructor?.BYTES_PER_ELEMENT ?? 4;
  const width = (target as THREE.InterleavedBuffer).stride ?? (target as THREE.BufferAttribute).itemSize;
  return placed(target.count * width * element);
}

function textureBytes(texture: THREE.Texture) {
  const mipmaps = texture.mipmaps as { data?: { byteLength: number } }[] | undefined;
  const mips = mipmaps?.reduce((sum, mip) => sum + (mip.data?.byteLength ?? 0), 0) ?? 0;
  if (mips) return mips;
  // CPU mips dropped after upload (world/sharedAssets.ts releaseTextureData): the GPU copy keeps its size.
  if (typeof texture.userData.releasedBytes === 'number') return texture.userData.releasedBytes;
  const image = texture.image as { width?: number; height?: number; data?: ArrayBufferView } | undefined;
  if (image?.data?.byteLength) return image.data.byteLength;
  // Uploaded RGBA8 with a mip chain.
  return image?.width && image.height ? Math.round(image.width * image.height * 4 * 4 / 3) : 0;
}

/** One measurement: shared buffers and texture sources count once across everything it visits. */
export class GpuMeter {
  private targets = new Set<object>();
  private textures = new Set<object>();
  constructor(private residency?: GpuResidency) {}
  /** GPU bytes of one mesh not yet counted in this measurement. */
  mesh(mesh: THREE.Mesh) {
    let bytes = 0;
    const geometry = mesh.geometry;
    const add = (target: Target | null | undefined) => {
      if (!target || this.targets.has(target)) return;
      this.targets.add(target); bytes += this.residency ? this.residency.buffer(target) : targetBytes(target);
    };
    if (geometry) {
      for (const attribute of Object.values(geometry.attributes)) {
        const interleaved = attribute as THREE.InterleavedBufferAttribute;
        add(interleaved.isInterleavedBufferAttribute ? interleaved.data : attribute as THREE.BufferAttribute);
      }
      add(geometry.index);
    }
    const instanced = mesh as unknown as THREE.InstancedMesh;
    if (instanced.isInstancedMesh) { add(instanced.instanceMatrix); add(instanced.instanceColor); }
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      for (const value of Object.values(material ?? {})) {
        const texture = value as THREE.Texture;
        if (!texture?.isTexture) continue;
        const key = texture.source ?? texture;
        if (this.textures.has(key)) continue;
        this.textures.add(key);
        if (!this.residency || this.residency.texture(texture)) bytes += textureBytes(texture);
      }
    }
    return bytes;
  }
  /** GPU bytes of a subtree not yet counted. */
  root(root: THREE.Object3D) {
    let bytes = 0;
    root.traverse(object => { if ((object as THREE.Mesh).isMesh) bytes += this.mesh(object as THREE.Mesh); });
    return bytes;
  }
}

/** A model's GPU bytes on its own (shared buffers and textures it uses count fully). */
export function modelGpuBytes(root: THREE.Object3D, residency?: GpuResidency) { return new GpuMeter(residency).root(root); }
