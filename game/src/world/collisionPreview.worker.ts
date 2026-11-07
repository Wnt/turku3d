import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
// @ts-expect-error three ships no declaration for its meshopt simplifier module (world/collisionPreviewArrays.ts Simplifier).
import { MeshoptSimplifier } from 'three/addons/libs/meshopt_simplifier.module.js';
import { collisionPreviewArrays } from './collisionPreviewArrays';

self.onmessage = async ({ data }: MessageEvent<{ id: number; buffers: ArrayBuffer[] }>) => {
  try {
    await MeshoptDecoder.ready; await MeshoptSimplifier.ready;
    const arrays = collisionPreviewArrays(data.buffers, MeshoptDecoder, MeshoptSimplifier);
    self.postMessage({ id: data.id, arrays }, { transfer: [arrays.position.buffer, arrays.color.buffer, arrays.index.buffer] });
  } catch (error) { self.postMessage({ id: data.id, error: String(error) }); }
};
