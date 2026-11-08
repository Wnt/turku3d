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

it('lights each clump with the ambient its ground was baked with (COLOR_1, canopy shade included)', () => {
  const library = natureFixture(), scene = new THREE.Scene(), model = new THREE.Group();
  const geometry = new THREE.PlaneGeometry(64, 64, 2, 2).rotateX(-Math.PI / 2);
  // R local ambient, G sky, B coverage per vertex: the west column under a canopy (sky 0.3), the rest open.
  const visibility = new Float32Array(geometry.attributes.position.count * 3);
  for (let i = 0; i < geometry.attributes.position.count; i++) {
    visibility.set([1, geometry.attributes.position.getX(i) < 0 ? 0.3 : 1, 1], i * 3);
  }
  geometry.setAttribute('color_1', new THREE.BufferAttribute(visibility, 3));
  const lawn = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
  lawn.userData = { layer: 'ground', material_key: 'draft_grass' }; model.add(lawn); scene.add(model);
  const field = new GrassField(library, 'medium'), steps = field.addSteps(model);
  while (!steps.next().done) { /* fixture */ }
  field.update(new THREE.PerspectiveCamera(65, 1, .1, 1000));
  const lawnDraw = (field.root.children as THREE.InstancedMesh[])[0];
  const ambient = lawnDraw.geometry.getAttribute('grassAmbient') as THREE.InstancedBufferAttribute;
  const shaded: number[] = [], open: number[] = [];
  for (let i = 0; i < lawnDraw.count; i++) {
    const x = lawnDraw.instanceMatrix.array[i * 16 + 12];
    if (x < -24) shaded.push(ambient.getX(i)); else if (x > 0) open.push(ambient.getX(i));
  }
  expect(shaded.length).toBeGreaterThan(50);
  // The western edge has sky 0.3: 1 - 0.7 * 0.7 = 0.51 at its vertices, interpolated to 1 at x = 0 (about 0.62 at -24 m, by triangle);
  // the open half keeps full ambient.
  expect(Math.min(...shaded)).toBeGreaterThan(0.5); expect(Math.max(...shaded)).toBeLessThan(0.66);
  expect(open.every(a => a === 1)).toBe(true);
  field.dispose();
});

it('grows tall tufts and meadow flowers on meadows and a lawn carpet on lawns', () => {
  const library = natureFixture(), scene = new THREE.Scene();
  const counts = (key: string) => {
    const model = new THREE.Group();
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(40, 40).rotateX(-Math.PI / 2), new THREE.MeshStandardMaterial());
    ground.userData = { layer: 'ground', material_key: key }; model.add(ground); scene.add(model);
    const field = new GrassField(library, 'medium'), steps = field.addSteps(model);
    while (!steps.next().done) { /* fixture */ }
    field.update(new THREE.PerspectiveCamera(65, 1, .1, 1000));
    // One draw per kind: lawn, clump, meadow, rosette.
    const result = (field.root.children as THREE.InstancedMesh[]).map(m => m.count);
    field.dispose(); scene.remove(model); return result;
  };
  const [lawn, clump, meadow] = counts('draft_grass'), total = lawn + clump + meadow;
  expect(lawn / total).toBeGreaterThan(0.6);
  const [mLawn, mClump, mMeadow] = counts('draft_grass_meadow'), mTotal = mLawn + mClump + mMeadow;
  expect(mLawn / mTotal).toBeLessThan(0.15);
  expect(mClump / mTotal).toBeGreaterThan(0.4);
  expect(mMeadow / mTotal).toBeGreaterThan(0.15);
});
