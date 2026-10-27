import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { IdPass } from '../../src/render/idPass';

test('layer object, facade and material identities survive the ID pass', () => {
  const root = new THREE.Group();
  root.userData = { layer: 'buildings', building_id: 'LR15' };
  for (const facade of [0, 1]) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(), [new THREE.MeshBasicMaterial(), new THREE.MeshBasicMaterial()]);
    mesh.userData = { object_id: 'wall', facade_index: facade, material_key: 'plaster' };
    mesh.material[1].userData = { material_key: 'window' };
    root.add(mesh);
  }
  const pass = new IdPass();
  pass.apply(root, 't0_0_0');
  expect(Object.values(pass.table)).toHaveLength(4);
  expect(Object.values(pass.table)).toContainEqual({ kind: 'building', id: 'wall', tile: 't0_0_0',
    layer: 'buildings', building: 'LR15', facade: '0', material: 'plaster' });
});

test('batched index ranges retain separate wall identities only in the QA pass', () => {
  const geometry = new THREE.BoxGeometry();
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  mesh.userData = { layer: 'buildings', objects: [
    { start: 0, count: 18, object_id: 'north', layer: 'buildings', building_id: 'LR15', facade_index: 0 },
    { start: 18, count: 18, object_id: 'south', layer: 'buildings', building_id: 'LR15', facade_index: 1 },
  ] };
  const pass = new IdPass();
  pass.apply(mesh, 't0_0_0');
  expect(mesh.geometry.groups).toEqual([
    { start: 0, count: 18, materialIndex: 0 }, { start: 18, count: 18, materialIndex: 1 },
  ]);
  expect(Object.values(pass.table).map(e => [e.id, e.facade])).toEqual([['north', '0'], ['south', '1']]);
  expect(mesh.geometry).not.toBe(geometry);
});
