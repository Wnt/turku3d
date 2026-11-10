import { FileLoader, type CompressedTexture, type Texture } from 'three';
import { AssetCache, sharedAssetKey, textureBytes, residentTexture, ktxTemplates, releasedMips } from './sharedAssets';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { loading, loadBytes } from '../core/loading';

let started = 0, completed = 0;
const templates = ktxTemplates as WeakMap<object, CompressedTexture>;
// Decoded KTX2 textures shared between tiles; 16 MiB (census: 62 MiB at a 64 MiB cap, mostly not shared again).
export const decodedTextures = new AssetCache<CompressedTexture>(16 * 1024 ** 2, textureBytes);
const decoded = decodedTextures;
// Keep immutable source bytes independently of decoded mips: eviction/upload can drop the latter before another
// tile needs a sampler variant. A cold start otherwise refetched ~15 MB of the same KTX2 content (loadsize).
export const encodedTextures = new AssetCache<ArrayBuffer>(48 * 1024 ** 2, buffer => buffer.byteLength);

/** Also counts embedded glTF textures, which have no separate Resource Timing entry. */
export class LoadingKtx extends KTX2Loader {
  override load(url: string, onLoad: (texture: CompressedTexture) => void,
    onProgress?: (event: ProgressEvent) => void, onError?: (error: unknown) => void): undefined {
    const resolved = this.manager.resolveURL(this.path + url);
    const key = sharedAssetKey(resolved);
    if (!key) { super.load(url, onLoad, onProgress, onError); return; }
    const content = `${JSON.stringify(this.workerConfig)}:${key}`;
    this.manager.itemStart(resolved);
    void decoded.get(content, () => new Promise<CompressedTexture>((resolve, reject) => {
      const resident = residentTexture(content);
      const template = resident && templates.get(resident.source);
      if (template) resolve(template);
      else void encodedTextures.get(key, () => new Promise<ArrayBuffer>((loaded, failed) => {
        const file = new FileLoader(this.manager).setPath(this.path).setCrossOrigin(this.crossOrigin)
          .setWithCredentials(this.withCredentials).setRequestHeader(this.requestHeader).setResponseType('arraybuffer');
        file.load(url, buffer => loaded(buffer as ArrayBuffer), onProgress, failed);
      })).then(buffer => {
        // The transcoder transfers its input to a worker. Keep the cache's original buffer attached.
        this.parse(buffer.slice(0), texture => { templates.set(texture.source, texture); resolve(texture); }, reject);
      }).catch(reject);
    })).then(template => {
      // glTF mutates sampler/UV/colour state; share decoded data, then canonicalize the final variant.
      const texture = template.clone();
      texture.userData.sharedAsset = content;
      texture.userData.sharedUrl = resolved; // decodes it again if its released mips are ever needed
      onLoad(texture); this.manager.itemEnd(resolved);
    }, error => { onError?.(error); this.manager.itemError(resolved); this.manager.itemEnd(resolved); });
  }

  override parse(buffer: ArrayBuffer, onLoad?: (texture: CompressedTexture) => void, onError?: (err: unknown) => void) {
    const done = loading.begin(`KTX2 transcode #${++started} · ${loadBytes(buffer.byteLength)}`);
    super.parse(buffer, texture => {
      done(`${++completed}/${started} textures decoded`); onLoad?.(texture);
    }, error => { done(`FAILED: ${String(error)}`); onError?.(error); });
  }
}

/** Decode a texture's source again into its own mips (it keeps them from then on), then upload it. */
const restoring = new WeakSet<Texture>();
let restores = 0;
export const textureRestores = { count: 0 };
function restoreTextureData(renderer: Parameters<LoadingKtx['detectSupport']>[0], texture: Texture) {
  if (restoring.has(texture)) return;
  restoring.add(texture);
  textureRestores.count++;
  const url = texture.userData.sharedUrl;
  // The first few say which path re-uploaded a released texture (expected: none).
  if (restores++ < 3) console.warn(`[textures] ${String(texture.userData.sharedAsset ?? texture.name)}: upload needs released mips, decoding again`, new Error().stack);
  if (typeof url !== 'string') return;
  sharedKtx(renderer).load(url, fresh => {
    texture.mipmaps = fresh.mipmaps; // same content, same transcoder target
    texture.userData.keepData = true;
    delete texture.userData.releasedBytes;
    restoring.delete(texture);
    texture.needsUpdate = true;
  }, undefined, error => { restoring.delete(texture); console.warn(`[textures] ${url}: ${String(error)}`); });
}

type Backend = { updateTexture?: (texture: Texture, options: unknown) => void };
const guarded = new WeakSet<object>();
/** No upload ever writes released mips (world/sharedAssets.ts releaseTextureData): the backend skips it, the
 *  texture decodes its source again, keeps that data and uploads once it is back. */
export function guardReleasedTextures(renderer: Parameters<LoadingKtx['detectSupport']>[0] & { backend?: Backend }) {
  const backend = renderer.backend;
  if (!backend?.updateTexture || guarded.has(backend)) return;
  guarded.add(backend);
  const update = backend.updateTexture.bind(backend);
  backend.updateTexture = (texture, options) => {
    if (releasedMips(texture)) { restoreTextureData(renderer, texture); return; }
    update(texture, options);
  };
}

// One transcoder worker pool for world, actors and ground on each renderer/device.
const loaders = new WeakMap<object, LoadingKtx>();
export function sharedKtx(renderer: Parameters<LoadingKtx['detectSupport']>[0]): LoadingKtx {
  let loader = loaders.get(renderer);
  if (!loader) {
    loader = new LoadingKtx().setTranscoderPath(`${import.meta.env.BASE_URL}basis/`).detectSupport(renderer);
    loaders.set(renderer, loader);
  }
  return loader;
}
