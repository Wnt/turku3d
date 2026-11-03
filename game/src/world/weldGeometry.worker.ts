import { weldArrays, type WeldInput } from './weldArrays';
self.onmessage = ({ data }: MessageEvent<{ id: number; input: WeldInput; tolerance: number }>) => {
  try {
    const output = weldArrays(data.input, data.tolerance);
    self.postMessage({ id: data.id, output }, { transfer: [...output.arrays.map(array => array.buffer), output.index.buffer] as ArrayBuffer[] });
  } catch (error) { self.postMessage({ id: data.id, error: String(error) }); }
};
