import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { PROP_LOD, PropLod, footprintDistance, furnitureMeshes } from '../../src/world/propLod';

function tile() {
  const model = new THREE.Group();
  const batch = (rows: Record<string, unknown>[], x: number) => {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(10, 1, 10).translate(x, 0.5, 5), new THREE.MeshStandardMaterial());
    mesh.userData = { layer: 'props', material_key: 'props_palette', objects: rows };
    model.add(mesh);
    return mesh;
  };
  const furniture = batch([{ prop_type: 'bench', start: 0, count: 36 }, { prop_type: 'bin', start: 36, count: 36 }], 5);
  const signs = batch([{ prop_type: 'sign', start: 0, count: 36 }], 5);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(250, 250), new THREE.MeshStandardMaterial());
  ground.userData = { layer: 'ground' };
  model.add(ground);
  return { model, furniture, signs, ground };
}

test('only furniture batches switch, never breakable sign poles or other layers', () => {
  const { model, furniture } = tile();
  expect(furnitureMeshes(model)).toEqual([furniture]);
});

test('furniture hides beyond the hide distance and returns within the show distance', () => {
  const { model, furniture, signs, ground } = tile();
  const lod = new PropLod(), camera = new THREE.PerspectiveCamera();
  lod.add(model); lod.add(model);
  expect(lod.size).toBe(1);
  const at = (x: number) => { camera.position.set(x, 1.7, 5); lod.update(camera); return furniture.visible; };
  expect(at(5)).toBe(true);
  expect(at(10 + PROP_LOD.hide - 1)).toBe(true);
  expect(at(10 + PROP_LOD.hide + 1)).toBe(false);
  expect(at(10 + PROP_LOD.show + 5)).toBe(false); // hysteresis: stays off until within `show`
  expect(at(10 + PROP_LOD.show - 1)).toBe(true);
  at(1000);
  expect([signs.visible, ground.visible]).toEqual([true, true]);
  lod.remove(model);
  expect(lod.size).toBe(0);
  expect(footprintDistance(new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(10, 1, 10)), 13, 14)).toBe(5);
});
