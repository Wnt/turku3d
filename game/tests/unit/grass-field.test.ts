import { expect, it } from 'vitest';
import * as THREE from 'three/webgpu';
import { GrassField } from '../../src/world/nature/grass';
import { natureFixture } from './natureFixture';

it('shares one grass budget across species and keeps nearest selection independent of tile arrival', () => {
  const library = natureFixture(), scene = new THREE.Scene();
  const models = ['west', 'east'].map(name => {
    const model = new THREE.Group(); model.name = name;
    const lawn = new THREE.Mesh(new THREE.PlaneGeometry(120, 120).rotateX(-Math.PI / 2), new THREE.MeshStandardMaterial());
    lawn.userData = { layer: 'ground', material_key: 'draft_grass' }; model.add(lawn); scene.add(model); return model;
  });
  const sample = (order: THREE.Object3D[]) => {
    const field = new GrassField(library, 'medium'); scene.add(field.root);
    for (const model of order) { const steps = field.addSteps(model); while (!steps.next().done) { /* fixture */ } }
    field.update(new THREE.PerspectiveCamera(65, 1, .1, 1000));
    const meshes = field.root.children as THREE.InstancedMesh[];
    expect(meshes.reduce((n, m) => n + m.count, 0)).toBe(12000);
    const matrices = meshes.map(m => Array.from(m.instanceMatrix.array.slice(0, m.count * 16)));
    field.dispose(); return matrices;
  };
  expect(sample(models)).toEqual(sample([...models].reverse()));
});


it('selects the same grass at a final pose regardless of the path into its camera cell', () => {
  const library = natureFixture(), scene = new THREE.Scene(), model = new THREE.Group();
  const lawn = new THREE.Mesh(new THREE.PlaneGeometry(160, 160).rotateX(-Math.PI / 2), new THREE.MeshStandardMaterial());
  lawn.userData = { layer: 'ground', material_key: 'draft_grass' }; model.add(lawn); scene.add(model);
  const sample = (start: number) => {
    const field = new GrassField(library, 'medium'), camera = new THREE.PerspectiveCamera(65, 1, .1, 1000);
    const steps = field.addSteps(model); while (!steps.next().done) { /* fixture */ }
    camera.position.set(start, 0, start); field.update(camera);
    camera.position.set(8, 0, 8); field.update(camera);
    const result = (field.root.children as THREE.InstancedMesh[]).map(m => Array.from(m.instanceMatrix.array.slice(0, m.count * 16)));
    field.dispose(); return result;
  };
  expect(sample(.1)).toEqual(sample(15.9));
});
