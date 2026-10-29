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
