import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { batchTileShadows, TILE_SHADOW_LAYER } from '../../src/world/shadowBatch';

const triangle = () => {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
  geometry.setIndex([0, 1, 2]);
  return geometry;
};

test('shadow chunks preserve buildings and instanced props without ground or vegetation', () => {
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


test('spatial shadow chunks retain every triangle and bound all vertices, including long crossing triangles', () => {
  const model = new THREE.Group();
  model.rotation.x = Math.PI / 2;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([
    0, 0, 0, 1, 0, 0, 0, 1, 0,
    200, 0, 0, 201, 0, 0, 200, 1, 0,
    -80, 0, 0, 100, 0, 0, 100, 100, 0,
  ], 3));
  const source = new THREE.Mesh(geometry); model.add(source);
  const root = batchTileShadows(model)!;
  const chunks: THREE.Mesh[] = []; root.traverse(object => chunks.push(object as THREE.Mesh));
  expect(chunks.length).toBeGreaterThan(1);
  const triangleIndices: number[] = [], point = new THREE.Vector3();
  for (const chunk of chunks) {
    const index = chunk.geometry.getIndex()!, positions = chunk.geometry.getAttribute('position');
    for (let i = 0; i < index.count; i++) {
      triangleIndices.push(index.getX(i));
      point.fromBufferAttribute(positions, index.getX(i));
      expect(chunk.geometry.boundingSphere!.containsPoint(point)).toBe(true);
    }
  }
  expect(triangleIndices.sort((a, b) => a - b)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8]);
  const distant = chunks.find(chunk => chunk.geometry.getIndex()!.getX(0) === 3)!;
  const camera = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.1, 100);
  camera.position.set(0, 0, 10); camera.lookAt(0, 0, 0); camera.updateMatrixWorld();
  root.updateMatrixWorld(true);
  const frustum = new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
  expect(frustum.intersectsObject(distant)).toBe(false);
  expect(frustum.intersectsObject(chunks[0])).toBe(true);
});
