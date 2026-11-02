import { expect, it } from 'vitest';
import * as THREE from 'three/webgpu';
import { GroundCover, lawnSamples } from '../../src/world/nature/groundCover';
import { VegetationLod } from '../../src/world/vegetationLod';
import { natureFixture } from './natureFixture';

const finish = <T>(steps: Generator<void, T>): T => {
  let result = steps.next();
  while (!result.done) result = steps.next();
  return result.value;
};
function lawn(size = 20, key = 'draft_grass') {
  const model = new THREE.Group(), geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([
    -size / 2, 2, -size, size / 2, 2, -size, size / 2, 4, 0, -size / 2, 4, 0,
  ], 3));
  geometry.setIndex([0, 1, 2, 0, 2, 3]);
  const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
  mesh.userData = { layer: 'ground', material_key: key, object_id: 'lawn:1' };
  model.add(mesh);
  return { model, mesh };
}

it('keeps deterministic clumps on measured lawn triangles, including their slope', () => {
  const { model } = lawn();
  model.position.set(250, 0, 250);
  const points = [...finish(lawnSamples(model)).values()].flat();
  expect(points.length).toBeGreaterThan(500);
  for (const p of points) {
    expect(p.x).toBeGreaterThanOrEqual(-10); expect(p.x).toBeLessThanOrEqual(10);
    expect(p.z).toBeGreaterThanOrEqual(-20); expect(p.z).toBeLessThanOrEqual(0);
    expect(p.y).toBeCloseTo(4 + p.z / 10);
  }
  expect([...finish(lawnSamples(model)).values()].flat()).toEqual(points);
});

it('never uses paving, water or vertical faces as lawn', () => {
  for (const key of ['draft_road', 'water', 'draft_concrete_slabs']) expect(finish(lawnSamples(lawn(20, key).model)).size).toBe(0);
  const { model, mesh } = lawn();
  mesh.rotation.x = Math.PI / 2;
  expect(finish(lawnSamples(model)).size).toBe(0);
});

it('shares the tile sample cap over late lawn meshes as well as early ones', () => {
  const { model } = lawn(250), second = lawn(250).mesh;
  second.position.x = 300; model.add(second);
  const points = [...finish(lawnSamples(model)).values()].flat();
  expect(points.length).toBeLessThanOrEqual(40000);
  expect(points.filter(p => p.x < 125).length).toBeGreaterThan(19000);
  expect(points.filter(p => p.x > 175).length).toBeGreaterThan(19000);
});

it('bounds visible grass across tiles, preserves stationary uploads and releases instances', () => {
  const library = natureFixture(), cover = new GroundCover('low');
  const scene = new THREE.Scene(), models = [lawn(60).model, lawn(60).model];
  for (const model of models) { scene.add(model); finish(cover.add(model, library)); }
  const camera = new THREE.PerspectiveCamera(80, 1, .1, 500); camera.position.set(0, 5, 3); camera.lookAt(0, 0, -15); camera.updateMatrixWorld();
  cover.update(camera);
  const meshes = models.map(m => m.getObjectByName('lawn-ground-cover') as THREE.InstancedMesh);
  expect(meshes.reduce((n, m) => n + (m.visible ? m.count : 0), 0)).toBeLessThanOrEqual(2500);
  expect(meshes.some(m => m.visible)).toBe(true);
  expect(meshes.every(m => !m.castShadow && m.receiveShadow)).toBe(true);
  const versions = meshes.map(m => m.instanceMatrix.version);
  cover.update(camera); expect(meshes.map(m => m.instanceMatrix.version)).toEqual(versions);
  models[0].visible = false; cover.update(camera); expect(meshes[0].visible).toBe(false);
  for (const model of models) cover.remove(model);
  expect(models.every(m => !m.getObjectByName('lawn-ground-cover'))).toBe(true);
});

it('replaces mapped hedge blocks at their segment length, height and orientation', () => {
  const library = natureFixture(), model = new THREE.Group(), scene = new THREE.Scene(); scene.add(model);
  const source = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial(), 1);
  source.userData = { layer: 'structures', material_key: 'draft_hedge' };
  const angle = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), .4);
  source.setMatrixAt(0, new THREE.Matrix4().compose(new THREE.Vector3(0, 0, -10), angle, new THREE.Vector3(2.5, 1.5, .65)));
  model.add(source);
  const vegetation = new VegetationLod('medium', library); vegetation.add(model);
  vegetation.update(new THREE.PerspectiveCamera(65, 1, .1, 100));
  const parts: THREE.InstancedMesh[] = [];
  model.traverse(o => { if (o.userData.nature === 'hedge' && o.visible) parts.push(o as THREE.InstancedMesh); });
  expect(source.visible).toBe(false); expect(parts.length).toBeGreaterThan(0);
  const matrix = new THREE.Matrix4(), at = new THREE.Vector3(), rotation = new THREE.Quaternion(), scale = new THREE.Vector3();
  parts[0].getMatrixAt(0, matrix); matrix.decompose(at, rotation, scale);
  expect(at.toArray()).toEqual([0, 0, -10]); expect(rotation.angleTo(angle)).toBeLessThan(.001);
  expect(scale.x).toBeCloseTo(1.25); expect(scale.y).toBeCloseTo(1.25); expect(scale.z).toBeCloseTo(.65 / .8);
  vegetation.remove(model); expect(source.visible).toBe(true);
});
