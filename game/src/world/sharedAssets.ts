import type * as THREE from 'three/webgpu';

/** Content identities survive differing relative paths; mutable URLs never enter these caches. */
export function sharedAssetKey(url: string): string | undefined {
  let parsed: URL;
  try { parsed = new URL(url, 'https://turku3d.invalid/'); } catch { return; }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return;
  // GLTFLoader concatenates resourcePath + ../shared; match the path the browser actually fetches.
  return /\/coarse\/(?:_v\/[^/]+\/)?shared\/([a-f0-9]{64}\.(?:ktx2|bin))$/.exec(parsed.pathname)?.[1];
}

/** Bounded session reuse and in-flight deduplication. Rejected loads are retryable. */
export class AssetCache<T> {
  private entries = new Map<string, { promise: Promise<T>; bytes: number }>();
  private bytes = 0;
  constructor(private maxBytes: number, private size: (value: T) => number) {}
  /** Resident bytes and entries (memory census). */
  get residentBytes() { return this.bytes; }
  get count() { return this.entries.size; }
  get(key: string, load: () => Promise<T>): Promise<T> {
    let entry = this.entries.get(key);
    if (entry) { this.entries.delete(key); this.entries.set(key, entry); return entry.promise; }
    entry = { promise: Promise.resolve().then(load), bytes: 0 };
    this.entries.set(key, entry);
    const current = entry;
    void current.promise.then(value => {
      current.bytes = this.size(value); this.bytes += current.bytes;
      for (const [key, old] of this.entries) {
        if (this.bytes <= this.maxBytes) break;
        if (!old.bytes) continue; // Never evict pending work and start a duplicate fetch.
        this.entries.delete(key); this.bytes -= old.bytes;
      }
    }, () => { this.entries.delete(key); });
    return current.promise;
  }
}
export const sharedBuffers = new AssetCache<ArrayBuffer>(32 * 1024 ** 2, buffer => buffer.byteLength);
export function textureBytes(texture: THREE.Texture): number {
  const mipmaps = texture.mipmaps as { data?: { byteLength: number } }[];
  return mipmaps?.reduce((sum, mip) => sum + (mip.data?.byteLength ?? 0), 0)
    || (texture.image as { data?: ArrayBufferView })?.data?.byteLength || 0;
}

/** Decoded KTX2 templates by texture source: LoadingKtx clones new variants from them (world/loadingKtx.ts). A
 *  template lives as long as any texture of its source, so its mips do too until releaseTextureData drops it. */
export const ktxTemplates = new WeakMap<object, THREE.Texture>();
/** CPU mips dropped after upload so far (memory census). */
export const textureRelease = { bytes: 0, count: 0 };
type Mip = { data: ArrayBufferView; width: number; height: number };

/** Texture surface arrays copy from these maps' mips later (render/surfaceArrays.ts): they keep them. */
export function keepTextureData(texture: THREE.Texture | null | undefined) { if (texture?.isTexture) texture.userData.keepData = true; }

/** Drop a shared compressed texture's CPU mips once `uploaded` (its GPU copy is current). Only resident textures:
 *  their GPU copy is reused by every tile, tile clones resolve to them before upload (shareModelTextures) and they
 *  are disposed only when no model uses them. The texture keeps mip sizes (empty data) and its byte size in
 *  `userData.releasedBytes`; its decoded template goes too, so a new variant decodes the source again. */
export function releaseTextureData(texture: THREE.Texture, uploaded: (texture: THREE.Texture) => boolean): number {
  // Only textures LoadingKtx can decode again (world/loadingKtx.ts guardReleasedTextures restores an upload).
  if (!managed.has(texture) || texture.userData.keepData || texture.userData.releasedBytes !== undefined
    || typeof texture.userData.sharedUrl !== 'string') return 0;
  const compressed = texture as THREE.CompressedTexture & { isCompressedArrayTexture?: boolean };
  if (!compressed.isCompressedTexture || compressed.isCompressedArrayTexture) return 0;
  const mipmaps = compressed.mipmaps as unknown as Mip[];
  if (!mipmaps?.length || !mipmaps.every(mip => ArrayBuffer.isView(mip.data) && mip.data.byteLength > 0) || !uploaded(texture)) return 0;
  let bytes = 0;
  // New mip objects: other variants and the template share the old ones.
  compressed.mipmaps = mipmaps.map(mip => {
    bytes += mip.data.byteLength;
    return { ...mip, data: new (mip.data.constructor as new (length: number) => ArrayBufferView)(0) };
  }) as unknown as THREE.CompressedTexture['mipmaps'];
  texture.userData.releasedBytes = bytes;
  ktxTemplates.delete(texture.source);
  textureRelease.bytes += bytes; textureRelease.count++;
  return bytes;
}

/** A compressed texture whose mips hold no data: released here, or a clone of one (Texture.copy copies userData and
 *  the mip list). Uploading it would write nothing (WebGPU validation error, black texture). */
export function releasedMips(texture: THREE.Texture): boolean {
  if (texture.userData.releasedBytes !== undefined) return true;
  const compressed = texture as THREE.CompressedTexture;
  return !!compressed.isCompressedTexture && Array.isArray(compressed.mipmaps)
    && (compressed.mipmaps as unknown as Partial<Mip>[]).some(mip => ArrayBuffer.isView(mip?.data) && mip.data.byteLength === 0);
}

/** releaseTextureData for every material slot texture of a committed model. */
export function releaseModelTextureData(model: THREE.Object3D, uploaded: (texture: THREE.Texture) => boolean): number {
  const materials = new Set<THREE.Material>();
  let bytes = 0;
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      if (materials.has(material)) continue;
      materials.add(material);
      for (const value of Object.values(material)) if ((value as THREE.Texture)?.isTexture) bytes += releaseTextureData(value as THREE.Texture, uploaded);
    }
  });
  return bytes;
}

interface Resident { key: string; texture: THREE.Texture; users: number; bytes: number }
const residents = new Map<string, Resident>();
const owners = new WeakMap<THREE.Object3D, Set<Resident>>();
const managed = new WeakSet<THREE.Texture>();
const MAX_IDLE_BYTES = 64 * 1024 ** 2;

function textureKey(texture: THREE.Texture): string | undefined {
  const content = texture.userData.sharedAsset;
  if (!content) return;
  // glTF assigns these after KTX decode. Different samplers/UVs/colour spaces must stay distinct.
  return JSON.stringify([content, texture.channel, texture.colorSpace, texture.wrapS, texture.wrapT,
    texture.magFilter, texture.minFilter, texture.anisotropy, texture.flipY, texture.premultiplyAlpha,
    texture.unpackAlignment, texture.generateMipmaps, texture.mapping, texture.format, texture.type,
    texture.offset.toArray(), texture.repeat.toArray(), texture.center.toArray(), texture.rotation,
    texture.matrixAutoUpdate, texture.matrixAutoUpdate ? null : texture.matrix.toArray()]);
}
function trimTextures() {
  let idle = [...residents.values()].reduce((sum, entry) => sum + (entry.users ? 0 : entry.bytes), 0);
  for (const [key, entry] of residents) {
    if (idle <= MAX_IDLE_BYTES) break;
    if (entry.users) continue;
    residents.delete(key); idle -= entry.bytes; entry.texture.dispose();
  }
}
/** Recover decoded data still retained by a live/idle GPU texture even after the byte cache evicts it. */
export function residentTexture(content: string): THREE.Texture | undefined {
  for (const entry of residents.values()) if (entry.texture.userData.sharedAsset === content) return entry.texture;
}
/** Runs before upload: identical final glTF textures use one object and one GPU allocation. */
export function shareModelTextures(model: THREE.Object3D) {
  const previous = owners.get(model);
  const owned = new Set<Resident>(previous), materials = new Set<THREE.Material>();
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      if (materials.has(material)) continue;
      materials.add(material);
      for (const [slot, value] of Object.entries(material)) {
        const texture = value as THREE.Texture;
        if (!texture?.isTexture) continue;
        const key = textureKey(texture);
        if (!key) continue;
        let entry = residents.get(key);
        if (!entry) {
          entry = { key, texture, users: 0, bytes: textureBytes(texture) };
          managed.add(texture);
        }
        residents.delete(key); residents.set(key, entry);
        (material as unknown as Record<string, unknown>)[slot] = entry.texture;
        owned.add(entry);
      }
    }
  });
  for (const entry of owned) if (!previous?.has(entry)) entry.users++;
  owners.set(model, owned); trimTextures();
}
/** A tile release never destroys a texture still in use by another tile. */
export function releaseModelTextures(model: THREE.Object3D, textures: Set<THREE.Texture>) {
  for (const texture of textures) if (!managed.has(texture)) texture.dispose();
  for (const entry of owners.get(model) ?? []) {
    entry.users--;
    // The moment the last owner leaves is this idle entry's most recent use.
    const key = entry.key;
    residents.delete(key); residents.set(key, entry);
  }
  owners.delete(model); trimTextures();
}
