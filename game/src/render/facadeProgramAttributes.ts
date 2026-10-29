import { BufferGeometry, InterleavedBuffer, InterleavedBufferAttribute } from 'three/webgpu';

/** WebGPU guarantees eight vertex buffers. Pack every program field into one buffer. */
export function interleaveProgram(geometry: BufferGeometry) {
  if (!geometry.hasAttribute('_kit_opening')) return;
  const names = Object.keys(geometry.attributes).filter(name => name.startsWith('_kit_'));
  const attributes = names.map(name => geometry.getAttribute(name));
  if (attributes[0] instanceof InterleavedBufferAttribute) return;
  const stride = attributes.reduce((sum, a) => sum + a.itemSize, 0);
  const count = attributes[0].count;
  const buffer = new InterleavedBuffer(new Float32Array(count * stride), stride);
  let offset = 0;
  attributes.forEach((attribute, index) => {
    for (let vertex = 0; vertex < count; vertex++) {
      for (let component = 0; component < attribute.itemSize; component++) {
        buffer.array[vertex * stride + offset + component] = attribute.getComponent(vertex, component);
      }
    }
    geometry.setAttribute(names[index], new InterleavedBufferAttribute(buffer, attribute.itemSize, offset));
    offset += attribute.itemSize;
  });
}
