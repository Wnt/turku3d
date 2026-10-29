import { expect, it } from 'vitest';
import { BufferGeometry, Float32BufferAttribute, InterleavedBufferAttribute } from 'three/webgpu';
import { interleaveProgram } from '../../src/render/facadeProgramAttributes';

it('keeps program fields exact and below the WebGPU vertex-buffer limit', () => {
  const geometry = new BufferGeometry();
  for (const name of ['position', 'normal', 'uv', '_kit_opening', '_kit_layout', '_kit_origin',
    '_kit_wall', '_kit_trim', '_kit_base', '_kit_floors', '_kit_size', '_kit_bands', '_kit_detail']) {
    geometry.setAttribute(name, new Float32BufferAttribute([1, 2, 3, 4, 5, 6, 7, 8], 4));
  }
  interleaveProgram(geometry);
  const buffers = new Set(Object.values(geometry.attributes).map(a => a instanceof InterleavedBufferAttribute ? a.data : a));
  expect(buffers.size).toBe(4);
  for (const a of Object.values(geometry.attributes)) {
    expect([a.getX(0), a.getY(0), a.getZ(1), a.getW(1)]).toEqual([1, 2, 7, 8]);
  }
  const first = geometry.getAttribute('_kit_opening');
  interleaveProgram(geometry);
  expect(geometry.getAttribute('_kit_opening')).toBe(first);
});

it('worker packing preserves normalized integer fields and independent source arrays', async () => {
  const { packProgram } = await import('../../src/render/packProgram');
  const first = new Uint8Array([0, 255, 128, 64]);
  const second = new Float32Array([9, 10]);
  const packed = packProgram([{ array: first, itemSize: 2, normalized: true },
    { array: second, itemSize: 1, normalized: false }]);
  expect([...packed]).toEqual([0, 1, 9, Math.fround(128 / 255), Math.fround(64 / 255), 10]);
  expect([...first]).toEqual([0, 255, 128, 64]); expect([...second]).toEqual([9, 10]);
});
