import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { StaticGroup } from '../../src/world/frozenTransforms';

/** A tile owner with placed building clones (wrapper > root > two meshes), as BuildingModels.attach builds them. */
function tile(placements: number, frozen: boolean) {
  const scene = new THREE.Scene(), owner = new THREE.Group(), geometry = new THREE.BoxGeometry();
  owner.add(new THREE.Group()); owner.children[0].position.set(250, 0, -250);
  const wrappers: THREE.Group[] = [];
  for (let i = 0; i < placements; i++) {
    const wrapper = frozen ? new StaticGroup() : new THREE.Group(), root = new THREE.Group();
    wrapper.position.set(i * 3, 0, i % 7); root.rotation.y = i * 0.1;
    for (const y of [1, 5]) { const mesh = new THREE.Mesh(geometry); mesh.position.y = y; mesh.scale.setScalar(1 + i % 3); root.add(mesh); }
    wrapper.add(root); owner.add(wrapper);
    if (frozen) (wrapper as StaticGroup).freeze();
    wrappers.push(wrapper);
  }
  scene.add(owner);
  const worlds = () => wrappers.flatMap(w => [w, ...w.children[0].children]).map(o => o.matrixWorld.toArray());
  return { scene, owner, wrappers, worlds };
}

test('frozen placements keep their world matrices and skip the per-frame recompute until the tile moves', () => {
  const frozen = tile(100, true), reference = tile(100, false);
  frozen.scene.updateMatrixWorld(); reference.scene.updateMatrixWorld();
  expect(frozen.worlds()).toEqual(reference.worlds());
  const compose = vi.spyOn(THREE.Matrix4.prototype, 'compose'), multiply = vi.spyOn(THREE.Matrix4.prototype, 'multiplyMatrices');
  frozen.scene.updateMatrixWorld();
  // Scene, owner and the owner's translated node only: not 100 wrappers, roots and 200 meshes.
  expect(compose).toHaveBeenCalledTimes(3);
  expect(multiply.mock.calls.length).toBeLessThanOrEqual(3);
  compose.mockClear(); multiply.mockClear();
  reference.scene.updateMatrixWorld();
  expect(compose).toHaveBeenCalledTimes(3 + 400);
  vi.restoreAllMocks();
  for (const t of [frozen, reference]) { t.owner.position.set(10, -2, 4); t.owner.rotation.y = 0.3; t.scene.updateMatrixWorld(); }
  expect(frozen.worlds()).toEqual(reference.worlds());
});

test('a flagged placement recomputes; updateWorldMatrix and new children still resolve', () => {
  const { scene, wrappers } = tile(3, true);
  scene.updateMatrixWorld();
  const wrapper = wrappers[1], mesh = wrapper.children[0].children[0];
  wrapper.matrix.makeTranslation(100, 0, 0); wrapper.matrixWorldNeedsUpdate = true;
  scene.updateMatrixWorld();
  expect(new THREE.Vector3().setFromMatrixPosition(mesh.matrixWorld).x).toBeCloseTo(100, 6);
  const added = new THREE.Mesh(); added.position.y = 3; wrapper.children[0].add(added);
  added.updateWorldMatrix(true, false);
  expect(new THREE.Vector3().setFromMatrixPosition(added.matrixWorld).toArray()).toEqual([100, 3, 0]);
  wrapper.matrix.makeTranslation(-5, 0, 0);
  scene.updateWorldMatrix(false, true);
  expect(new THREE.Vector3().setFromMatrixPosition(mesh.matrixWorld).x).toBeCloseTo(-5, 6);
});
