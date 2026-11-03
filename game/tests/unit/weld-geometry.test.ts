import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
import { weldGeometry, weldGeometryAsync } from '../../src/world/weldGeometry';

function soup(triangles: number, seed = 7) {
  let state = seed;
  const random = () => ((state = (Math.imul(state, 1664525) + 1013904223) >>> 0) / 2 ** 32);
  // A small vertex pool repeated across triangles, with sub-tolerance jitter on some copies.
  const pool = Array.from({ length: Math.max(3, triangles >> 1) }, () => [random() * 4 - 2, random() * 3, random() * 4 - 2, random(), random(), random(), random()]);
  const position: number[] = [], normal: number[] = [], color: number[] = [], surface: number[] = [];
  for (let i = 0; i < triangles * 3; i++) {
    const [x, y, z, a, b, c, d] = pool[Math.floor(random() * pool.length)], jitter = random() < .3 ? 2e-6 : 0;
    position.push(x + jitter, y, z - jitter); normal.push(a, b, c); color.push(c, a, b); surface.push(d, a, b, c);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(position, 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normal, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(color, 3));
  geometry.setAttribute('fleetSurface', new THREE.Float32BufferAttribute(surface, 4));
  return geometry;
}

function same(actual: THREE.BufferGeometry, expected: THREE.BufferGeometry) {
  expect(Object.keys(actual.attributes)).toEqual(Object.keys(expected.attributes));
  for (const name of Object.keys(expected.attributes)) {
    expect(actual.attributes[name].itemSize).toBe(expected.attributes[name].itemSize);
    expect(Array.from(actual.attributes[name].array)).toEqual(Array.from(expected.attributes[name].array));
  }
  expect(actual.index!.array.constructor).toBe(expected.index!.array.constructor);
  expect(Array.from(actual.index!.array)).toEqual(Array.from(expected.index!.array));
}

test('worker weld reproduces r186 mergeVertices exactly for fleet bake tolerances', async () => {
  for (const tolerance of [1e-4, 1e-5]) for (const triangles of [1, 40, 900]) {
    same(weldGeometry(soup(triangles), tolerance), mergeVertices(soup(triangles), tolerance));
    same(await weldGeometryAsync(soup(triangles), tolerance), mergeVertices(soup(triangles), tolerance));
  }
  const indexed = mergeVertices(soup(300), 1e-2);
  same(weldGeometry(indexed, 1e-5), mergeVertices(indexed, 1e-5));
});

test('index width follows BufferGeometry.setIndex at 65535 and unsupported layouts fall back', () => {
  const wide = soup(40000, 3); // ~60000 distinct pool vertices plus jitter variants
  const reference = mergeVertices(wide.clone(), 1e-7);
  same(weldGeometry(wide, 1e-7), reference);
  expect(reference.index!.array).toBeInstanceOf(reference.attributes.position.count > 65535 ? Uint32Array : Uint16Array);
  const quantized = soup(10);
  quantized.setAttribute('normal', new THREE.BufferAttribute(new Int8Array(90).fill(64), 3, true));
  same(weldGeometry(quantized), mergeVertices(quantized));
});
