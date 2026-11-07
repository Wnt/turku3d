import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { boundsSteps } from '../../src/render/geometryBounds';

const reference = (geometry: THREE.BufferGeometry) => {
  const copy = geometry.clone(); copy.computeBoundingBox(); copy.computeBoundingSphere();
  return { box: copy.boundingBox!, sphere: copy.boundingSphere! };
};

test('sliced bounds match computeBoundingBox/Sphere for float and quantized positions', () => {
  const float = new THREE.TorusKnotGeometry(3, 0.7, 40, 6).translate(120, -4, 33).rotateY(0.7);
  const quantized = new THREE.BufferGeometry();
  const values = new Int16Array(3000).map((_, i) => ((i * 7919) % 65535) - 32767);
  quantized.setAttribute('position', new THREE.BufferAttribute(values, 3, true));
  for (const geometry of [float, quantized]) {
    const steps = [...boundsSteps(geometry)];
    expect(steps).toHaveLength(1);
    const { box, sphere } = reference(geometry);
    expect(geometry.boundingBox).toEqual(box);
    expect(geometry.boundingSphere!.center).toEqual(sphere.center);
    expect(geometry.boundingSphere!.radius).toBe(sphere.radius);
  }
});
