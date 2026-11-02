import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { waterCells } from '../../src/world/vegetationLod';

function collect(model: THREE.Object3D) {
  const steps = waterCells(model);
  for (;;) { const step = steps.next(); if (step.done) return step.value; }
}

// Independent old sampling contract: every oriented triangle edge contributes the same 4 m
// centres and square bank reach, even across negative coordinates and nested transforms.
function reference(model: THREE.Object3D, mesh: THREE.Mesh) {
  model.updateWorldMatrix(true, true);
  const matrix = model.matrixWorld.clone().invert().multiply(mesh.matrixWorld);
  const p = mesh.geometry.getAttribute('position'), index = mesh.geometry.index;
  const cells = new Set<string>(), count = index?.count ?? p.count;
  const point = (i: number) => new THREE.Vector3().fromBufferAttribute(p, index?.getX(i) ?? i).applyMatrix4(matrix);
  for (let i = 0; i < count; i++) {
    const a = point(i), b = point(i % 3 === 2 ? i - 2 : i + 1);
    const steps = Math.max(1, Math.ceil(a.distanceTo(b) / 4));
    for (let k = 0; k <= steps; k++) {
      const x = Math.floor((a.x + (b.x - a.x) * k / steps) / 4);
      const z = Math.floor((a.z + (b.z - a.z) * k / steps) / 4);
      for (let dz = -4; dz <= 4; dz++) for (let dx = -4; dx <= 4; dx++) cells.add(`${x + dx},${z + dz}`);
    }
  }
  return cells;
}

test.each([true, false])('water bank coverage preserves repeated and shared edges (indexed=%s)', indexed => {
  const model = new THREE.Group(), parent = new THREE.Group();
  model.position.set(130, 7, -60); model.rotation.y = .3;
  parent.position.set(-4, 2, 9); parent.rotation.y = -.45; parent.scale.set(1.2, 1, .7);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([-21.3, 0, -6.1, 35.7, 0, 4.2, -4.2, 0, 35.8, 44.1, 0, 29.7], 3));
  geometry.setIndex(Array.from({ length: 50 }, () => [0, 1, 2, 2, 1, 3]).flat());
  const surface = indexed ? geometry : geometry.toNonIndexed();
  const mesh = new THREE.Mesh(surface, new THREE.MeshStandardMaterial());
  mesh.userData.surface = 'water'; mesh.position.set(2, 0, -3);
  parent.add(mesh); model.add(parent);
  expect(collect(model)).toEqual(reference(model, mesh));
  // Repeating the exact same geometry cannot extend the bank band.
  parent.add(mesh.clone());
  expect(collect(model)).toEqual(reference(model, mesh));
  surface.dispose(); if (surface !== geometry) geometry.dispose();
});


test('dense repeated triangles expand each sampled bank cell once', () => {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 4, 0, 0, 0, 0, 4], 3));
  geometry.setIndex(Array.from({ length: 100 }, () => [0, 1, 2]).flat());
  const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
  mesh.userData.surface = 'water';
  const expected = reference(mesh, mesh);
  const add = vi.spyOn(Set.prototype, 'add');
  let cells: Set<string>, count: number;
  try { cells = collect(mesh); count = add.mock.calls.length; }
  finally { add.mockRestore(); }
  expect(cells!).toEqual(expected);
  expect(count!).toBeLessThan(1000); // The old repeated dilation adds >50,000 entries.
  geometry.dispose();
});
