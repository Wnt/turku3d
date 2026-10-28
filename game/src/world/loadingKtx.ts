import type { CompressedTexture } from 'three';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { loading, loadBytes } from '../core/loading';

let started = 0, completed = 0;

/** Also counts embedded glTF textures, which have no separate Resource Timing entry. */
export class LoadingKtx extends KTX2Loader {
  override parse(buffer: ArrayBuffer, onLoad?: (texture: CompressedTexture) => void, onError?: (err: unknown) => void) {
    const done = loading.begin(`KTX2 transcode #${++started} · ${loadBytes(buffer.byteLength)}`);
    super.parse(buffer, texture => {
      done(`${++completed}/${started} textures decoded`); onLoad?.(texture);
    }, error => { done(`FAILED: ${String(error)}`); onError?.(error); });
  }
}
