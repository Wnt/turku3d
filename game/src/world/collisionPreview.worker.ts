import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { collisionPreviewArrays } from './collisionPreviewArrays';

self.onmessage = async ({ data }: MessageEvent<{ id: number; buffers: ArrayBuffer[] }>) => {
  try {
    await MeshoptDecoder.ready;
    const arrays = collisionPreviewArrays(data.buffers, MeshoptDecoder);
    self.postMessage({ id: data.id, arrays }, { transfer: [arrays.position.buffer, arrays.color.buffer, arrays.index.buffer] });
  } catch (error) { self.postMessage({ id: data.id, error: String(error) }); }
};
