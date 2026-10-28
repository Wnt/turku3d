import type { CompressedTexture } from 'three';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { loading, loadBytes } from '../core/loading';

/** Also counts embedded glTF textures, which have no separate Resource Timing entry. */
export class LoadingKtx extends KTX2Loader {
  private started = 0;
  private completed = 0;
  override parse(buffer: ArrayBuffer, onLoad?: (texture: CompressedTexture) => void, onError?: (err: unknown) => void) {
    const done = loading.begin(`KTX2 transcode #${++this.started} · ${loadBytes(buffer.byteLength)}`);
    super.parse(buffer, texture => {
      done(`${++this.completed}/${this.started} textures decoded`); onLoad?.(texture);
    }, error => { done(`FAILED: ${String(error)}`); onError?.(error); });
  }
}
