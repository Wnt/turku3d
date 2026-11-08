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
  const instance = lawnDraw.geometry.getAttribute('grassInstance') as THREE.InstancedBufferAttribute;
  const shaded: number[] = [], open: number[] = [];
  for (let i = 0; i < lawnDraw.count; i++) {
    const x = lawnDraw.instanceMatrix.array[i * 16 + 12];
    if (x < -24) shaded.push(instance.getZ(i)); else if (x > 0) open.push(instance.getZ(i));
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

it('draws tufts as opaque blades whose mean colour sits on the meadow ground, sinking with their scale', async () => {
  const { tuftGeometry } = await import('../../src/world/nature/grassBlades');
  const tuft = tuftGeometry(), position = tuft.getAttribute('position'), color = tuft.getAttribute('color');
  tuft.computeBoundingBox();
  expect(tuft.index!.count / 3).toBe(14 * 5);
  expect(tuft.boundingBox!.min.y).toBeCloseTo(-0.02, 3);
  expect(tuft.boundingBox!.max.y).toBeGreaterThan(0.35); expect(tuft.boundingBox!.max.y).toBeLessThan(0.5);
  // Area-weighted albedo over the blade triangles against the meadow finish's mean (0.103, 0.13, 0.033).
  const mean = [0, 0, 0]; let total = 0;
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  for (let t = 0; t < tuft.index!.count; t += 3) {
    const [i, j, k] = [0, 1, 2].map(o => tuft.index!.getX(t + o));
    a.fromBufferAttribute(position, i); b.fromBufferAttribute(position, j); c.fromBufferAttribute(position, k);
    const area = b.clone().sub(a).cross(c.clone().sub(a)).length() / 2;
    for (let ch = 0; ch < 3; ch++) mean[ch] += area * (color.getComponent(i, ch) + color.getComponent(j, ch) + color.getComponent(k, ch)) / 3;
    total += area;
  }
  const albedo = mean.map(v => v / total);
  expect(albedo[1]).toBeGreaterThan(albedo[0]); expect(albedo[0]).toBeGreaterThan(albedo[2]);
  for (const [ours, ground] of albedo.map((v, i) => [v, [0.103, 0.13, 0.033][i]])) expect(Math.abs(ours - ground)).toBeLessThan(0.035);

  const library = natureFixture(), scene = new THREE.Scene(), model = new THREE.Group();
  const meadow = new THREE.Mesh(new THREE.PlaneGeometry(30, 30).rotateX(-Math.PI / 2), new THREE.MeshStandardMaterial());
  meadow.userData = { layer: 'ground', material_key: 'draft_grass_meadow' }; model.add(meadow); scene.add(model);
  const field = new GrassField(library, 'medium'), steps = field.addSteps(model);
  while (!steps.next().done) { /* fixture */ }
  field.update(new THREE.PerspectiveCamera(65, 1, .1, 1000));
  const tufts = (field.root.children as THREE.InstancedMesh[]).find(m => m.userData.nature === 'grass_clump')!;
  expect(tufts.material).toBe(library.materials.grassBlade);
  expect(library.materials.grassBlade.alphaTest).toBe(0);
  expect(library.materials.grassBlade.transparent).toBe(false);
  const instance = tufts.geometry.getAttribute('grassInstance') as THREE.InstancedBufferAttribute;
  const matrix = new THREE.Matrix4(), s = new THREE.Vector3();
  for (const i of [0, Math.floor(tufts.count / 2), tufts.count - 1]) {
    tufts.getMatrixAt(i, matrix); s.setFromMatrixScale(matrix);
    expect(instance.getW(i)).toBeCloseTo(s.y, 5);
  }
  field.dispose();
});

it('keeps every grass draw within WebGPU\'s 8 vertex buffers: one per-instance vec4, no instanceColor', () => {
  // The card clumps in nature.glb carry position, normal, uv, colour and _wind. With the instance matrix,
  // instanceColor and grassAmbient the Air's Chrome counted 9 buffers (the renderer adds one of its own) and failed
  // both grass pipelines: no grass was drawn at all. Packed into one vec4 the cards need 8.
  const CARD = ['position', 'normal', 'uv', 'color', '_wind'];
  const library = natureFixture(), scene = new THREE.Scene(), model = new THREE.Group();
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(30, 30).rotateX(-Math.PI / 2), new THREE.MeshStandardMaterial());
  ground.userData = { layer: 'ground', material_key: 'draft_grass_meadow' }; model.add(ground); scene.add(model);
  const field = new GrassField(library, 'medium'), steps = field.addSteps(model);
  while (!steps.next().done) { /* fixture */ }
  field.update(new THREE.PerspectiveCamera(65, 1, .1, 1000));
  for (const mesh of field.root.children as THREE.InstancedMesh[]) {
    expect(mesh.instanceColor).toBeNull();
    const instanced = Object.entries(mesh.geometry.attributes).filter(([, a]) => (a as THREE.InstancedBufferAttribute).isInstancedBufferAttribute);
    expect(instanced.map(([name]) => name)).toEqual(['grassInstance']);
    const own = mesh.userData.nature === 'grass_clump' ? Object.keys(mesh.geometry.attributes).filter(n => n !== 'grassInstance').length : CARD.length;
    expect(own + instanced.length + 1 /* instance matrix */ + 1 /* renderer */).toBeLessThanOrEqual(8);
  }
  field.dispose();
});

it('grows bilberry, fern and herb plants on forest floors, by floor type, and no lawn cards there', async () => {
  const { forestKind } = await import('../../src/world/nature/grass');
  const share = (floor: 'conifer' | 'broadleaf' | 'moss') => {
    const counts = new Map<number, number>();
    for (let i = 0; i < 4000; i++) { const k = forestKind(floor, (i % 40) / 39, ((i * 0.6180339887) % 1)); counts.set(k, (counts.get(k) ?? 0) + 1); }
    return (k: number) => (counts.get(k) ?? 0) / 4000;
  };
  const conifer = share('conifer'), broadleaf = share('broadleaf'), moss = share('moss');
  // Kinds: 1 blade tuft, 4 dwarf shrub, 5 fern, 6 herb; -1 bare floor. No lawn (0), meadow (2) or rosette cards (3).
  for (const s of [conifer, broadleaf, moss]) for (const card of [0, 2, 3]) expect(s(card)).toBe(0);
  expect(conifer(4)).toBeGreaterThan(0.4);
  expect(broadleaf(6)).toBeGreaterThan(0.3);
  expect(moss(5)).toBeGreaterThan(0.25);
  for (const s of [conifer, broadleaf]) expect(s(-1)).toBeGreaterThan(0.15);

  const library = natureFixture(), scene = new THREE.Scene(), model = new THREE.Group();
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(30, 30).rotateX(-Math.PI / 2), new THREE.MeshStandardMaterial());
  floor.userData = { layer: 'ground', material_key: 'draft_forest_floor_conifer' }; model.add(floor); scene.add(model);
  const field = new GrassField(library, 'medium'), steps = field.addSteps(model);
  while (!steps.next().done) { /* fixture */ }
  field.update(new THREE.PerspectiveCamera(65, 1, .1, 1000));
  const drawn: Record<string, number> = Object.fromEntries((field.root.children as THREE.InstancedMesh[]).map(m => [m.userData.nature as string, m.count]));
  expect(drawn.grass_lawn).toBe(0);
  expect(drawn.groundcover_shrub).toBeGreaterThan(50);
  // About 0.55 x 2.5 plants per m2 on the 900 m2 floor, a third of them bare.
  const total = Object.values(drawn).reduce((a, b) => a + b, 0);
  expect(total).toBeGreaterThan(400); expect(total).toBeLessThan(1300);
  for (const mesh of field.root.children as THREE.InstancedMesh[]) {
    if (!/shrub|fern|herb/.test(mesh.userData.nature)) continue;
    expect(mesh.material).toBe(library.materials.grassBlade);
    const box = mesh.geometry.boundingBox!;
    expect(box.max.y).toBeGreaterThan(0.08); expect(box.max.y).toBeLessThan(0.65);
    expect(mesh.geometry.index!.count / 3).toBeLessThan(260);
  }
  field.dispose();
});
