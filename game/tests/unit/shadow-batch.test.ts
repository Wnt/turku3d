import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { batchTileShadows, TILE_SHADOW_LAYER } from '../../src/world/shadowBatch';

const triangle = () => {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
  geometry.setIndex([0, 1, 2]);
  return geometry;
};

test('one shadow mesh preserves buildings and instanced props without ground or vegetation', () => {
  const model = new THREE.Group();
  model.position.set(10, 0, 20);
  const building = new THREE.Mesh(triangle(), new THREE.MeshBasicMaterial());
  building.userData.layer = 'buildings';
  building.position.x = 2;
  const props = new THREE.InstancedMesh(triangle(), new THREE.MeshBasicMaterial(), 2);
  props.userData.layer = 'props';
  props.setMatrixAt(0, new THREE.Matrix4().makeTranslation(0, 2, 0));
  props.setMatrixAt(1, new THREE.Matrix4().makeTranslation(3, 0, 0));
  const trees = new THREE.InstancedMesh(triangle(), new THREE.MeshBasicMaterial(), 20);
  trees.userData.layer = 'vegetation';
  const ground = new THREE.Mesh(triangle(), new THREE.MeshBasicMaterial());
  ground.userData.layer = 'ground';
  model.add(building, props, trees, ground);

  const shadow = batchTileShadows(model)!;
  expect(shadow.geometry.getIndex()!.count).toBe(9);
  expect(shadow.geometry.getAttribute('position').count).toBe(9);
  expect(shadow.castShadow).toBe(true);
  const camera = new THREE.PerspectiveCamera();
  expect(shadow.layers.test(camera.layers)).toBe(false);
  const shadowCamera = new THREE.OrthographicCamera();
  shadowCamera.layers.enable(TILE_SHADOW_LAYER);
  expect(shadow.layers.test(shadowCamera.clone().layers)).toBe(true);
  expect(building.layers.test(shadowCamera.layers)).toBe(true);
  expect([building, props, trees, ground].map(mesh => mesh.castShadow)).toEqual([false, false, false, false]);
  const pos = shadow.geometry.getAttribute('position');
  expect([0, 3, 6].map(i => [pos.getX(i), pos.getY(i), pos.getZ(i)])).toEqual([
    [2, 0, 0], [0, 2, 0], [3, 0, 0],
  ]);
  shadow.geometry.dispose();
  (shadow.material as THREE.Material).dispose();
});
