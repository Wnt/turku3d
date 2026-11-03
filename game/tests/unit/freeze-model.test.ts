import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { StaticGroup, freezeModel } from '../../src/world/frozenTransforms';

const box = () => new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial());

test('a frozen model keeps its world matrices without per-frame work; props, leaves and later children stay live', () => {
  const scene = new THREE.Scene(); scene.updateMatrix(); scene.matrixAutoUpdate = false;
  const model = new THREE.Group(); model.position.set(500, 2, -300); model.rotation.y = 0.3;
  const ground = box(); ground.position.set(10, 0, 5);
  const group = new THREE.Group(); group.position.set(-20, 0, 0); const relief = box(); relief.position.y = 3; group.add(relief);
  const pole = box(); pole.userData.prop_type = 'sign'; pole.position.set(4, 0, 4);
  const water = box(); const target = new THREE.Object3D(); target.position.set(1, 0, 0); water.add(target);
  const placement = new StaticGroup(); placement.position.set(30, 0, 0); placement.add(box()); placement.freeze();
  model.add(ground, group, pole, water, placement); scene.add(model);
  freezeModel(model);
  const expected = new Map<THREE.Object3D, THREE.Matrix4>();
  model.traverse(o => expected.set(o, o.matrixWorld.clone()));
  expect([model, ground, group, relief, water].every(o => !o.matrixAutoUpdate)).toBe(true);
  expect([pole, target].every(o => o.matrixAutoUpdate)).toBe(true);
  const compose = THREE.Object3D.prototype.updateMatrix;
  let composed = 0;
  THREE.Object3D.prototype.updateMatrix = function (this: THREE.Object3D) { composed++; compose.call(this); };
  try {
    for (let frame = 0; frame < 3; frame++) scene.updateMatrixWorld();
    // Only the live prop and leaf compose each frame.
    expect(composed).toBe(3 * 2);
  } finally { THREE.Object3D.prototype.updateMatrix = compose; }
  model.traverse(o => expect(o.matrixWorld.equals(expected.get(o)!)).toBe(true));
  // Live nodes follow their own changes: a broken pole falls, the reflection target moves, a later child appears.
  pole.position.y = -1; target.position.set(2, 0, 0); const later = box(); later.position.set(0, 7, 0); model.add(later);
  scene.updateMatrixWorld();
  const world = (o: THREE.Object3D) => new THREE.Vector3().setFromMatrixPosition(o.matrixWorld);
  expect(world(pole).toArray().map(v => +v.toFixed(4))).toEqual(new THREE.Vector3(4, -1, 4).applyMatrix4(model.matrixWorld).toArray().map(v => +v.toFixed(4)));
  expect(world(target).x).toBeCloseTo(new THREE.Vector3(2, 0, 0).applyMatrix4(water.matrixWorld).x);
  expect(world(later).y).toBeCloseTo(9);
  // An explicit flag still updates a frozen node.
  ground.position.x = 11; ground.updateMatrix(); scene.updateMatrixWorld();
  expect(world(ground).toArray()).toEqual(new THREE.Vector3(11, 0, 5).applyMatrix4(model.matrixWorld).toArray());
});
