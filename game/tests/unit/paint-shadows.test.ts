import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { batchTileShadows } from '../../src/world/shadowBatch';

test('road paint lies on the ground: it receives shadows and casts none', () => {
  const model = new THREE.Group();
  const paint = new THREE.Mesh(new THREE.PlaneGeometry(), new THREE.MeshStandardMaterial({ vertexColors: true }));
  paint.userData.layer = 'markings';
  const wall = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
  wall.userData.layer = 'buildings';
  model.add(paint, wall);
  const proxy = batchTileShadows(model)!;
  expect(paint.castShadow).toBe(false);
  expect(proxy.userData.shadowTriangles).toBe(new THREE.BoxGeometry().index!.count / 3);
});
