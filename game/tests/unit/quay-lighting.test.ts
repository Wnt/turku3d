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
