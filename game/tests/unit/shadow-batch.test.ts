import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { batchTileShadows, TILE_SHADOW_CHUNK_LAYER, TILE_SHADOW_LAYER, TILE_SHADOW_WHOLE_LAYER } from '../../src/world/shadowBatch';
import { cascadeLayerMask } from '../../src/render/environment';

const triangle = () => {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
  geometry.setIndex([0, 1, 2]);
  return geometry;
};

test('distance-LOD facade instances retain live shadows instead of a permanent flattened copy', () => {
  const model=new THREE.Group(),lod=new THREE.LOD();
  const modules=new THREE.InstancedMesh(triangle(),new THREE.MeshBasicMaterial(),2);
  modules.userData.facadeModules=true; modules.castShadow=true;
  lod.addLevel(modules,0); lod.addLevel(new THREE.Object3D(),110); model.add(lod);
  expect(batchTileShadows(model)).toBeNull();
  expect(modules.castShadow).toBe(true);
  lod.update(new THREE.PerspectiveCamera(60,1,.1,1000));
  expect(modules.visible).toBe(true);
});

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
  // The outer cascades draw the whole-tile caster, the nearest one its chunks; both see layer 0 and the proxies.
  const near = new THREE.OrthographicCamera(), outer = new THREE.OrthographicCamera();
  near.layers.mask = cascadeLayerMask(0); outer.layers.mask = cascadeLayerMask(2);
  const chunk = shadow.children[0] as THREE.Mesh;
  expect([shadow.layers.test(outer.layers), shadow.layers.test(near.layers)]).toEqual([true, false]);
  expect([chunk.layers.test(near.layers), chunk.layers.test(outer.layers)]).toEqual([true, false]);
  expect(shadow.layers.isEnabled(TILE_SHADOW_WHOLE_LAYER) && chunk.layers.isEnabled(TILE_SHADOW_CHUNK_LAYER)).toBe(true);
  for (const cascade of [near, outer]) {
    expect(building.layers.test(cascade.layers)).toBe(true);
    expect(cascade.layers.isEnabled(TILE_SHADOW_LAYER)).toBe(true);
  }
  // One shared vertex and index buffer: the chunk draws a range of the whole caster's buffers.
  expect(chunk.geometry.getAttribute('position')).toBe(shadow.geometry.getAttribute('position'));
  expect(chunk.geometry.getIndex()).toBe(shadow.geometry.getIndex());
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
  const chunks = root.children as THREE.Mesh[];
  expect(chunks.length).toBeGreaterThan(1);
  // The chunks' ranges tile the whole caster's index buffer exactly once.
  const ranges = chunks.map(c => [c.geometry.drawRange.start, c.geometry.drawRange.count]).sort((a, b) => a[0] - b[0]);
  let next = 0;
  for (const [start, count] of ranges) { expect(start).toBe(next); next += count; }
  expect(next).toBe(root.geometry.getIndex()!.count);
  expect([root.geometry.drawRange.start, root.geometry.drawRange.count]).toEqual([0, next]);
  for (const chunk of chunks) expect(root.geometry.boundingSphere!.containsPoint(chunk.geometry.boundingSphere!.center)).toBe(true);
  const triangleVertices: number[][] = [], point = new THREE.Vector3();
  for (const chunk of chunks) {
    const index = chunk.geometry.getIndex()!, positions = chunk.geometry.getAttribute('position');
    for (let i = chunk.geometry.drawRange.start; i < chunk.geometry.drawRange.start + chunk.geometry.drawRange.count; i++) {

      point.fromBufferAttribute(positions, index.getX(i));
      triangleVertices.push(point.toArray());
      expect(chunk.geometry.boundingSphere!.containsPoint(point)).toBe(true);
    }
  }
  expect(triangleVertices.sort((a, b) => a[0] - b[0] || a[1] - b[1])).toEqual([[-80,0,0],[0,0,0],[0,1,0],[1,0,0],[100,0,0],[100,100,0],[200,0,0],[200,1,0],[201,0,0]]);
  const distant = chunks.find(chunk => chunk.geometry.boundingBox!.min.x >= 200)!;
  const camera = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.1, 100);
  camera.position.set(0, 0, 10); camera.lookAt(0, 0, 0); camera.updateMatrixWorld();
  root.updateMatrixWorld(true);
  const frustum = new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
  expect(frustum.intersectsObject(distant)).toBe(false);
  expect(frustum.intersectsObject(chunks[0])).toBe(true);
  expect(frustum.intersectsObject(root)).toBe(true);
});
