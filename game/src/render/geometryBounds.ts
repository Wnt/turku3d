import * as THREE from 'three/webgpu';

/** computeBoundingBox + computeBoundingSphere of a geometry built on the main thread while streaming (merged static
 *  batches and ground surfaces), in two vertex passes with a yield between, not three in one step: the sphere is
 *  centred on the box centre as computeBoundingSphere centres it (no morph attributes here). Plain float positions
 *  are read from their array. */
export function* boundsSteps(geometry: THREE.BufferGeometry): Generator<void> {
  const position = geometry.getAttribute('position') as THREE.BufferAttribute | THREE.InterleavedBufferAttribute;
  const box = geometry.boundingBox ?? new THREE.Box3();
  const plain = position instanceof THREE.BufferAttribute && position.array instanceof Float32Array
    && !position.normalized && position.itemSize === 3 ? position.array : null;
  if (plain) {
    let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
    for (let i = 0, n = position.count * 3; i < n; i += 3) {
      const x = plain[i], y = plain[i + 1], z = plain[i + 2];
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
      if (z < z0) z0 = z; if (z > z1) z1 = z;
    }
    box.min.set(x0, y0, z0); box.max.set(x1, y1, z1);
  } else box.setFromBufferAttribute(position as THREE.BufferAttribute);
  geometry.boundingBox = box;
  yield;
  const sphere = geometry.boundingSphere ?? new THREE.Sphere();
  const center = box.getCenter(sphere.center), point = new THREE.Vector3();
  let radius = 0;
  if (plain) {
    const { x, y, z } = center;
    for (let i = 0, n = position.count * 3; i < n; i += 3) {
      const dx = x - plain[i], dy = y - plain[i + 1], dz = z - plain[i + 2];
      radius = Math.max(radius, dx * dx + dy * dy + dz * dz);
    }
  } else for (let i = 0; i < position.count; i++) radius = Math.max(radius, center.distanceToSquared(point.fromBufferAttribute(position, i)));
  sphere.radius = Math.sqrt(radius);
  geometry.boundingSphere = sphere;
}
