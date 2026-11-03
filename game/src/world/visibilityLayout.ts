import { BOX_STRIDE, MAX_SLOT_BOXES, SLOT_STRIDE } from './occlusionRaster';

/** SharedArrayBuffer layout shared by the main thread and the visibility worker. */
export const MAX_SLOTS = 4096;
export const CONTROL = { cameraSeq: 0, resultSeq: 1, slotCount: 2 } as const;
export const CAMERA = { vp: 0, eye: 16, near: 19, size: 20 } as const;

const WORDS = MAX_SLOTS / 32;
const bytes = {
  control: 8 * 4,
  camera: CAMERA.size * 8,
  resultEye: 2 * 8 * 8,
  results: 2 * WORDS * 4,
  slots: MAX_SLOTS * SLOT_STRIDE * 4,
  boxes: MAX_SLOTS * MAX_SLOT_BOXES * BOX_STRIDE * 4,
  boxCounts: MAX_SLOTS,
};

export function bufferBytes() { return Object.values(bytes).reduce((sum, n) => sum + n, 0); }

export function layout(buffer: SharedArrayBuffer) {
  let at = 0;
  const take = (n: number) => { const start = at; at += n; return start; };
  const control = new Int32Array(buffer, take(bytes.control), 8);
  const camera = new Float64Array(buffer, take(bytes.camera), CAMERA.size);
  const eyes = take(bytes.resultEye);
  const resultEye = [new Float64Array(buffer, eyes, 8), new Float64Array(buffer, eyes + 64, 8)];
  const words = take(bytes.results);
  const results = [new Uint32Array(buffer, words, WORDS), new Uint32Array(buffer, words + WORDS * 4, WORDS)];
  const slots = new Float32Array(buffer, take(bytes.slots), MAX_SLOTS * SLOT_STRIDE);
  const boxes = new Float32Array(buffer, take(bytes.boxes), MAX_SLOTS * MAX_SLOT_BOXES * BOX_STRIDE);
  const boxCounts = new Uint8Array(buffer, take(bytes.boxCounts), MAX_SLOTS);
  return { control, camera, resultEye, results, slots, boxes, boxCounts };
}
