import { RainSurfaceIndex, type RainMeshData } from './surfaceIndex';
const models = new Map<string, RainSurfaceIndex>();
self.onmessage = (event: MessageEvent<
  { kind: 'add'; id: string; meshes: RainMeshData[] } | { kind: 'remove'; id: string }
  | { kind: 'query'; sequence: number; x: number; z: number; size: number; cell: number }
>) => {
  const message = event.data;
  if (message.kind === 'add') { models.set(message.id, new RainSurfaceIndex(message.meshes)); self.postMessage({ kind: 'changed' }); return; }
  if (message.kind === 'remove') { models.delete(message.id); self.postMessage({ kind: 'changed' }); return; }
  const heights = new Float32Array(message.size * message.size * 2);
  for (let j = 0; j < message.size; j++) for (let i = 0; i < message.size; i++) {
    const x = message.x + (i + .5) * message.cell, z = message.z + (j + .5) * message.cell;
    let top = -800, flat = false;
    for (const model of models.values()) {
      const hit = model.height(x, z);
      if (hit && hit.y > top) { top = hit.y; flat = hit.flat; }
    }
    heights[(j * message.size + i) * 2] = top;
    heights[(j * message.size + i) * 2 + 1] = flat ? 1 : 0;
  }
  self.postMessage({ kind: 'field', sequence: message.sequence, x: message.x, z: message.z, heights }, { transfer: [heights.buffer] });
};
