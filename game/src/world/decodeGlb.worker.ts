import { decodeGlb } from './decodeGlb';
self.onmessage = ({ data }: MessageEvent<{ id: number; buffer: ArrayBuffer }>) => {
  try {
    const result = decodeGlb(data.buffer);
    self.postMessage({ id: data.id, ...result }, { transfer: result.binary ? [result.binary] : [] });
  } catch (error) { self.postMessage({ id: data.id, error: String(error) }); }
};
