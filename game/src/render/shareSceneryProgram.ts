import * as THREE from 'three/webgpu';

/** Static facade instructions are identical before/after the world-space bake.
 * Share their CPU bytes, retaining each source buffer's identity and GPU allocation.
 * The contents, count and layout do not change, so no upload/version bump is needed.
 * Detach before restoring sources: one surviving tile must not retain a whole cell. */
export function shareSceneryProgram(meshes: readonly THREE.Mesh[], merged: THREE.BufferGeometry) {
  const fields = Object.keys(merged.attributes).filter(name => name.startsWith('_kit_'));
  const packed = merged.attributes[fields[0]] as THREE.InterleavedBufferAttribute | undefined;
  const shared: { buffer: THREE.InterleavedBuffer; view: Float32Array }[] = [];
  if (!packed?.isInterleavedBufferAttribute || !(packed.data.array instanceof Float32Array)) return () => {};
  const seen = new Set<THREE.InterleavedBuffer>();
  let offset = 0;
  for (const mesh of meshes) {
    const geometry = mesh.geometry, count = geometry.attributes.position.count;
    const attribute = geometry.attributes[fields[0]] as THREE.InterleavedBufferAttribute | undefined;
    const buffer = attribute?.isInterleavedBufferAttribute ? attribute.data : undefined;
    if (buffer && !seen.has(buffer) && buffer.usage === THREE.StaticDrawUsage
      && buffer.array instanceof Float32Array && buffer.stride === packed.data.stride
      && buffer.array.length === count * buffer.stride
      && fields.every(name => {
        const source = geometry.attributes[name] as THREE.InterleavedBufferAttribute;
        const target = merged.attributes[name] as THREE.InterleavedBufferAttribute;
        return source?.data === buffer && !source.normalized && source.offset === target.offset
          && source.itemSize === target.itemSize;
      })) {
      const view = packed.data.array.subarray(offset * buffer.stride, (offset + count) * buffer.stride);
      // Preserve the attribute owner: source disposal must never destroy the cell's GPU buffer.
      buffer.array = view;
      shared.push({ buffer, view }); seen.add(buffer);
    }
    offset += count;
  }
  return () => {
    for (const { buffer, view } of shared) if (buffer.array === view) buffer.array = view.slice();
    shared.length = 0;
  };
}
