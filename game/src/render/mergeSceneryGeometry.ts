import * as THREE from 'three/webgpu';
import type { TypedArray } from 'three/src/core/BufferAttribute.js';

/** Bake compatible scenery sources directly into final buffers. In particular,
 * facade instructions never pass through cloned, deinterleaved and repacked copies. */
export function mergeSceneryGeometry(meshes: readonly THREE.Mesh[]): THREE.BufferGeometry {
  const first = meshes[0].geometry;
  const vertices = meshes.reduce((sum, mesh) => sum + mesh.geometry.attributes.position.count, 0);
  const geometry = new THREE.BufferGeometry();
  const fields = first.hasAttribute('_kit_opening')
    ? Object.keys(first.attributes).filter(name => name.startsWith('_kit_')).sort() : [];
  if (fields.length) {
    const stride = fields.reduce((sum, name) => sum + first.attributes[name].itemSize, 0);
    const buffer = new THREE.InterleavedBuffer(new Float32Array(vertices * stride), stride);
    let offset = 0;
    for (const name of fields) {
      const size = first.attributes[name].itemSize;
      geometry.setAttribute(name, new THREE.InterleavedBufferAttribute(buffer, size, offset));
      offset += size;
    }
  }
  for (const [name, attribute] of Object.entries(first.attributes)) {
    if (geometry.hasAttribute(name)) continue;
    const floats = ['position', 'normal', 'tangent'].includes(name)
      || (attribute as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute;
    const ArrayType = floats ? Float32Array : attribute.array.constructor as {new(length: number): TypedArray};
    geometry.setAttribute(name, new THREE.BufferAttribute(new ArrayType(vertices * attribute.itemSize),
      attribute.itemSize, floats ? false : attribute.normalized));
  }
  if (first.index) {
    const count = meshes.reduce((sum, mesh) => sum + mesh.geometry.index!.count, 0);
    geometry.setIndex(new THREE.BufferAttribute(vertices > 65535 ? new Uint32Array(count) : new Uint16Array(count), 1));
  }
  const point = new THREE.Vector3(), normalMatrix = new THREE.Matrix3();
  let vertexOffset = 0, indexOffset = 0;
  for (const mesh of meshes) {
    const source = mesh.geometry;
    normalMatrix.getNormalMatrix(mesh.matrixWorld);
    for (const [name, destination] of Object.entries(geometry.attributes)) {
      const attribute = source.attributes[name];
      if (name === 'position' || name === 'normal' || name === 'tangent') {
        for (let i = 0; i < attribute.count; i++) {
          point.fromBufferAttribute(attribute, i);
          if (name === 'position') point.applyMatrix4(mesh.matrixWorld);
          else if (name === 'normal') point.applyNormalMatrix(normalMatrix);
          else point.transformDirection(mesh.matrixWorld);
          destination.setXYZ(vertexOffset + i, point.x, point.y, point.z);
          if (attribute.itemSize === 4) destination.setW(vertexOffset + i, attribute.getW(i));
        }
      } else {
        for (let i = 0; i < attribute.count; i++) for (let c = 0; c < attribute.itemSize; c++)
          destination.setComponent(vertexOffset + i, c, attribute.getComponent(i, c));
      }
    }
    if (source.index) for (let i = 0; i < source.index.count; i++)
      geometry.index!.setX(indexOffset++, source.index.getX(i) + vertexOffset);
    vertexOffset += source.attributes.position.count;
  }
  geometry.computeBoundingBox(); geometry.computeBoundingSphere();
  return geometry;
}
