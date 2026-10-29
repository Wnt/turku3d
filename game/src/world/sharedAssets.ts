import type * as THREE from 'three/webgpu';

/** Content identities survive differing relative paths; mutable URLs never enter these caches. */
export function sharedAssetKey(url: string): string | undefined {
  return /\/coarse\/shared\/([a-f0-9]{64}\.(?:ktx2|bin))(?:[?#].*)?$/.exec(url)?.[1];
}

/** Bounded session reuse and in-flight deduplication. Rejected loads are retryable. */
export class AssetCache<T> {
  private entries = new Map<string, { promise: Promise<T>; bytes: number }>();
  private bytes = 0;
  constructor(private maxBytes: number, private size: (value: T) => number) {}
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
  if (owners.has(model)) return;
  const owned = new Set<Resident>(), materials = new Set<THREE.Material>();
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
  for (const entry of owned) entry.users++;
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
