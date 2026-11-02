import { expect, it } from 'vitest';
import * as THREE from 'three/webgpu';
import { natureLibraryFromScene } from '../../src/world/nature/library';
import { natureFixture } from './natureFixture';

it('keeps wind phase shared across late-loaded libraries and precise at UTC timestamps', () => {
  const first = natureFixture(false), late = natureFixture(false);
  const utc = 1793592000.125;
  first.materials.setTime(utc - 90);
  first.materials.setTime(utc);
  late.materials.setTime(utc);
  expect(first.materials.windTime.value).toBe(late.materials.windTime.value);
  expect(first.materials.windTime.value).toBeLessThan(Math.PI * 200);
  for (const frequency of [0.42, 1.15, 7.3]) {
    expect(Math.sin(Math.fround(first.materials.windTime.value) * frequency))
      .toBeCloseTo(Math.sin(utc * frequency), 3);
  }
  late.materials.setTime(utc + 1 / 60);
  expect(late.materials.windTime.value).not.toBe(first.materials.windTime.value);
  first.materials.dispose(); late.materials.dispose();
});

it('keeps all wind oscillators continuous across wrapping and freezes deterministic QA', () => {
  const live = natureFixture(false), still = natureFixture();
  const period = Math.PI * 200;
  live.materials.setTime(period - 0.001);
  const before = live.materials.windTime.value;
  live.materials.setTime(period + 0.001);
  for (const frequency of [0.42, 1.15, 7.3]) {
    expect(Math.abs(Math.sin(before * frequency) - Math.sin(live.materials.windTime.value * frequency))).toBeLessThan(0.015);
  }
  still.materials.setTime(1793592000);
  expect(still.materials.windTime.value).toBe(0);
  live.materials.dispose(); still.materials.dispose();
});

it('bakes quantized nature.glb positions and their node transform into float asset geometry', () => {
  const root = new THREE.Group();
  const material = (name: string) => Object.assign(new THREE.MeshStandardMaterial({ map: new THREE.Texture(), normalMap: new THREE.Texture() }), { name });
  for (const name of ['nature_bark', 'nature_impostor', 'nature_wood', 'nature_metal', 'nature_granite', 'nature_chain']) root.add(new THREE.Mesh(new THREE.BufferGeometry(), material(name)));
  // KHR_mesh_quantization: normalized int16 positions in [-1, 1], dequantized by the node's scale and offset.
  const geometry = new THREE.BufferGeometry();
  const position = new THREE.BufferAttribute(new Int16Array([-32767, 0, 0, 32767, 0, 0, 0, 32767, 0]), 3, true);
  geometry.setAttribute('position', position);
  geometry.setAttribute('normal', new THREE.BufferAttribute(new Int8Array([0, 0, 127, 0, 0, 127, 0, 0, 127]), 3, true));
  const mesh = new THREE.Mesh(geometry, material('nature_leaves'));
  mesh.userData = { asset: 'grass_clump', lod: 0, kind: 'grass' };
  mesh.scale.set(0.5, 2, 0.5); mesh.position.set(0, 1, 0);
  root.add(mesh);
  const asset = natureLibraryFromScene(root, null, true).asset('grass_clump');
  const baked = asset.lods[0].parts[0].geometry.getAttribute('position');
  expect(baked.array).toBeInstanceOf(Float32Array);
  expect([baked.getX(0), baked.getY(0), baked.getX(1), baked.getY(2)].map(v => +v.toFixed(3))).toEqual([-0.5, 1, 0.5, 3]);
  expect(asset.lods[0].parts[0].geometry.getAttribute('normal').getZ(0)).toBeCloseTo(1);
});
