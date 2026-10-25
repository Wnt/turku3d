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
