import type { CompressedTexture } from 'three';
import { AssetCache, sharedAssetKey, textureBytes, residentTexture } from './sharedAssets';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { loading, loadBytes } from '../core/loading';

let started = 0, completed = 0;
const templates = new WeakMap<object, CompressedTexture>();
const decoded = new AssetCache<CompressedTexture>(64 * 1024 ** 2, textureBytes);

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
      else super.load(url, texture => { templates.set(texture.source, texture); resolve(texture); }, onProgress, reject);
    })).then(template => {
      // glTF mutates sampler/UV/colour state; share decoded data, then canonicalize the final variant.
      const texture = template.clone();
      texture.userData.sharedAsset = content;
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
