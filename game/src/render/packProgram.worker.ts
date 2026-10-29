import { packProgram, type ProgramField } from './packProgram';
self.onmessage = ({ data }: MessageEvent<{ id: number; fields: ProgramField[] }>) => {
  try {
    const packed = packProgram(data.fields);
    self.postMessage({ id: data.id, packed }, { transfer: [packed.buffer] });
  } catch (error) { self.postMessage({ id: data.id, error: String(error) }); }
};
