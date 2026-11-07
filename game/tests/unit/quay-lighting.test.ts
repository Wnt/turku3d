import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { quayWashMaterial } from '../../src/render/quayLighting';
import { nightSurface } from '../../src/render/nightSurface';
import { nightUniforms } from '../../src/render/nightLights';

const nodesOf = (material: THREE.MeshStandardNodeMaterial) => {
  const nodes = new Set<unknown>(); material.emissiveNode!.traverse(n => nodes.add(n)); return nodes;
};

test('lit quay stone keeps its stone maps and follows the shared night schedule', () => {
  const source = new THREE.MeshStandardMaterial();
  source.map = new THREE.DataTexture(new Uint8Array([100, 100, 100, 255]), 1, 1);
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const wall = quayWashMaterial(source, renderer);
  expect(wall.map).toBe(source.map);
  expect(wall.vertexColors).toBe(false);
  expect(nodesOf(wall).has(nightUniforms.night)).toBe(true);
});

test('quay LED bars are ordinary night_ emitters', () => {
  const bar = nightSurface(new THREE.MeshStandardNodeMaterial({ emissive: new THREE.Color(1, .74, .44) }), 'night_quay_bar');
  expect(bar.userData.nightSurface).toBe(true);
  expect(nodesOf(bar).has(nightUniforms.night)).toBe(true);
});

test('materials of one night key share one emissive graph (node identity is part of the program key)', () => {
  const walls = [0.2, 0.6].map(r => nightSurface(new THREE.MeshStandardNodeMaterial({ color: new THREE.Color(r, .3, .3) }), 'night_landmark_wall'));
  expect(walls[0].emissiveNode).toBe(walls[1].emissiveNode);
  expect(walls[0].customProgramCacheKey()).toBe(walls[1].customProgramCacheKey());
  const bar = nightSurface(new THREE.MeshStandardNodeMaterial(), 'night_quay_bar');
  expect(bar.emissiveNode).not.toBe(walls[0].emissiveNode);
});

test('draft walls share one night-window graph per variant (with or without an ORM)', async () => {
  const { draftWindows } = await import('../../src/render/nightLights');
  const withOrm = [0, 1].map(() => draftWindows(new THREE.MeshStandardNodeMaterial({ roughnessMap: new THREE.Texture() })));
  const plain = [0, 1].map(() => draftWindows(new THREE.MeshStandardNodeMaterial()));
  expect(withOrm[0].emissiveNode).toBe(withOrm[1].emissiveNode);
  expect(plain[0].emissiveNode).toBe(plain[1].emissiveNode);
  expect(withOrm[0].emissiveNode).not.toBe(plain[0].emissiveNode);
  expect(withOrm[0].customProgramCacheKey()).toBe(withOrm[1].customProgramCacheKey());
});
