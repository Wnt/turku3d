import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { float } from 'three/tsl';
import { bakedAmbientContext } from '../../src/render/bakedVisibility';

type GetAO = (input: THREE.Node<'float'> | null, builder: { material: THREE.Material; geometry: THREE.BufferGeometry }) => THREE.Node<'float'> | null;
const getAO = (screen: THREE.Node<'float'> | null = null) => (bakedAmbientContext(screen) as unknown as { value: { getAO: GetAO } }).value.getAO;

test('baked COLOR_1 occludes only opaque indirect light of geometry that carries it', () => {
  const baked = new THREE.BufferGeometry();
  baked.setAttribute('color_1', new THREE.BufferAttribute(new Uint8Array([255, 128, 255, 255]), 4, true));
  const plain = new THREE.BufferGeometry(), opaque = new THREE.MeshStandardNodeMaterial();
  const glass = new THREE.MeshStandardNodeMaterial({ transparent: true });
  // No bake and no GTAO: the material's own AO (or none) is untouched, so its program does not change.
  expect(getAO()(null, { material: opaque, geometry: plain })).toBeNull();
  const own = float(0.5);
  expect(getAO()(own, { material: opaque, geometry: plain })).toBe(own);
  expect(getAO()(own, { material: glass, geometry: baked })).toBe(own);
  expect(getAO()(null, { material: opaque, geometry: baked })).not.toBeNull();
  expect(getAO()(own, { material: opaque, geometry: baked })).not.toBe(own);
  // GTAO tiers keep their screen-space term on unbaked surfaces.
  const screen = float(0.8);
  expect(getAO(screen)(null, { material: opaque, geometry: plain })).toBe(screen);
});
