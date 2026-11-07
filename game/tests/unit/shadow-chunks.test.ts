import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { shadowChunks } from '../../src/world/shadowChunks';
import { COLLISION_TILE } from '../../src/core/origin';

test('shadow chunks: triangles by world 62.5 m cell centroid, first-seen order, exact chunk bounds', () => {
  let seed = 7; const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const vertices = 3000, positions = new Float32Array(vertices * 3).map((_, i) => (random() - 0.4) * (i % 3 === 1 ? 30 : 400));
  const indices = new Uint16Array(9000).map(() => Math.floor(random() * vertices));
  const world = new THREE.Matrix4().compose(new THREE.Vector3(-1234.5, 3, 777.25),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0.6, 0)), new THREE.Vector3(1, 1, 1));
  const { whole, chunks } = shadowChunks(positions, indices, world);
  const sorted = whole.index!.array;
  // Reference: three's Vector3/Box3 arithmetic per triangle.
  const point = new THREE.Vector3(), cell = (t: number) => {
    const c = new THREE.Vector3();
    for (let j = 0; j < 3; j++) c.add(point.fromArray(positions, indices[t * 3 + j] * 3));
    c.multiplyScalar(1 / 3).applyMatrix4(world);
    return `${Math.floor(c.x / COLLISION_TILE)},${Math.floor(c.z / COLLISION_TILE)}`;
  };
  const order: string[] = [], triangles = new Map<string, number[]>();
  for (let t = 0; t < indices.length / 3; t++) {
    const key = cell(t);
    if (!triangles.has(key)) { triangles.set(key, []); order.push(key); }
    triangles.get(key)!.push(t);
  }
  expect(chunks).toHaveLength(order.length);
  let start = 0;
  order.forEach((key, n) => {
    const box = new THREE.Box3(), expected: number[] = [];
    for (const t of triangles.get(key)!) for (let j = 0; j < 3; j++) {
      expected.push(indices[t * 3 + j]); box.expandByPoint(point.fromArray(positions, indices[t * 3 + j] * 3));
    }
    expect(chunks[n].drawRange).toEqual({ start, count: expected.length });
    expect([...sorted.slice(start, start + expected.length)]).toEqual(expected);
    expect(chunks[n].boundingBox).toEqual(box);
    start += expected.length;
  });
  const all = new THREE.Box3();
  for (const index of indices) all.expandByPoint(point.fromArray(positions, index * 3));
  expect(whole.boundingBox).toEqual(all);
});
