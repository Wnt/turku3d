import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
// @ts-expect-error r186 internal node manager has no DefinitelyTyped declaration.
import NodeManager from 'three/src/renderers/common/nodes/NodeManager.js';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { GameLighting } from '../../src/render/lighting';
import { Environment } from '../../src/render/environment';
import { MaterialStyler } from '../../src/render/materials';
import { modelTextures } from '../../src/render/textureUploads';
import { PreparationBudget } from '../../src/render/preparationBudget';
import { QUALITY } from '../../src/render/quality';
import { SURFACE_LAYER } from '../../src/render/surfaceArrays';

/** A KTX2-decoded shared library texture: compressed mips kept on the CPU, content hash from the asset URL. */
function compressedTexture(asset: string, size: number, srgb: boolean, seed: number) {
  const mipmaps = [];
  for (let s = size, level = 0; s >= 4; s >>= 1, level++) mipmaps.push({ data: new Uint8Array(s * s).fill((seed * 16 + level) & 255), width: s, height: s });
  const texture = new THREE.CompressedTexture(mipmaps as unknown as THREE.CompressedTextureMipmap[], size, size, THREE.RGBA_ASTC_4x4_Format);
  texture.userData.sharedAsset = `${asset}.ktx2`;
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping; texture.repeat.set(0.5, 0.5);
  if (srgb) texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}
function surfaceMaterial(name: string, seed: number, albedo = 32) {
  const orm = compressedTexture(`${name}-orm`, 8, false, seed + 2);
  return new THREE.MeshStandardMaterial({ map: compressedTexture(`${name}-albedo`, albedo, true, seed), normalMap: compressedTexture(`${name}-normal`, 16, false, seed + 1),
    roughnessMap: orm, metalnessMap: orm, aoMap: orm, roughness: 0.9 });
}
function surfaceMesh(key: string, material: THREE.Material, x: number, objects?: Record<string, unknown>[]) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([x, 0, 0, x + 1, 0, 0, x, 0, 1, x + 1, 0, 1], 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute([0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0], 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1, 1, 1], 2));
  geometry.setAttribute('color_1', new THREE.BufferAttribute(new Uint8Array(16).fill(200), 4, true));
  geometry.setIndex([0, 2, 1, 1, 2, 3]);
  const mesh = new THREE.Mesh(geometry, material);
  mesh.userData = { layer: 'ground', material_key: key, ...(objects ? { objects } : {}) };
  return mesh;
}
/** A tile as GLTFLoader returns it: library surfaces under the tile's translated root node, own material objects. */
function tile() {
  const model = new THREE.Group(), root = new THREE.Group();
  root.position.set(250, 0, -250); model.add(root);
  const asphalt = surfaceMaterial('asphalt', 1);
  root.add(surfaceMesh('draft_asphalt_worn', asphalt, 0), surfaceMesh('draft_asphalt_worn', asphalt, 2, [{ object_id: 'road:1', start: 0, count: 6 }]),
    surfaceMesh('draft_granite_curb', surfaceMaterial('granite', 4), 4), surfaceMesh('draft_concrete_slabs', surfaceMaterial('slabs', 7), 6),
    surfaceMesh('draft_grass', surfaceMaterial('grass', 10), 8), surfaceMesh('draft_fieldstone', surfaceMaterial('fieldstone', 13, 64), 10),
    surfaceMesh('draft_gravel', surfaceMaterial('gravel', 16), 12));
  return { model, root };
}
function world() {
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false;
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  renderer.lighting = new GameLighting(QUALITY.medium.lights);
  const scene = new THREE.Scene(), env = new Environment(scene, renderer, 'medium');
  scene.environment = Object.assign(new THREE.DataTexture(new Uint8Array(4 * 64 * 64), 64, 64), { mapping: THREE.CubeUVReflectionMapping });
  const styler = new MaterialStyler(renderer, env, 'medium', true);
  const uploads: THREE.Texture[] = [], updates: { texture: THREE.Texture; uploaded: boolean; layers: number[] }[] = [];
  // The real queue uploads in budgeted tasks after rendered frames; record what it would upload.
  const surfaces = (styler as unknown as { surfaces: { upload: (t: THREE.Texture) => Promise<void>;
    update: (t: THREE.Texture, bytes: number, change: () => void) => Promise<void> } }).surfaces;
  surfaces.upload = async texture => { uploads.push(texture); };
  surfaces.update = async (texture, _bytes, change) => {
    change();
    updates.push({ texture, uploaded: uploads.includes(texture), layers: [...(texture as THREE.CompressedArrayTexture).layerUpdates] });
    (texture as THREE.CompressedArrayTexture).clearLayerUpdates(); // as r186's upload does
  };
  return { renderer, scene, env, styler, uploads, updates };
}

test('a tile\'s wet library surfaces draw as one mesh over city-wide texture arrays; others keep their own draw', async () => {
  const { styler, uploads } = world();
  const a = tile();
  styler.apply(a.model);
  await styler.surfacesReady();
  const names = a.root.children.map(child => (child as THREE.Mesh).userData.material_key ?? child.name);
  // Wet surfaces in one draw, grass and gravel (no wet film) in another; fieldstone has another texture size.
  expect(names).toEqual(['draft_fieldstone', 'surface-array', 'surface-array-dry']);
  const merged = a.root.children.find(child => child.name === 'surface-array') as THREE.Mesh;
  const dry = a.root.children.find(child => child.name === 'surface-array-dry') as THREE.Mesh;
  expect([...new Set(dry.geometry.getAttribute(SURFACE_LAYER).array as Float32Array)]).toEqual([3, 4]);
  expect((dry.material as THREE.MeshStandardNodeMaterial).userData.weatherWetness).toBeUndefined();
  const layers = [...(merged.geometry.getAttribute(SURFACE_LAYER).array as Float32Array)];
  expect(layers).toEqual([0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2]); // the two asphalt meshes share one layer
  expect([...merged.geometry.index!.array]).toEqual([0, 2, 1, 1, 2, 3, 4, 6, 5, 5, 6, 7, 8, 10, 9, 9, 10, 11, 12, 14, 13, 13, 14, 15]);
  expect(merged.geometry.getAttribute('position').getX(4)).toBe(2); // local to the translated root, like its sources
  expect((merged.userData.objects as { object_id?: string; start: number }[]).find(row => row.object_id === 'road:1')!.start).toBe(6);
  const material = merged.material as THREE.MeshStandardNodeMaterial;
  expect(material.userData.weatherWetness).toBe(true);
  // Each array slice holds exactly that surface's compressed mips.
  const albedo = (styler as unknown as { surfaces: { arrays: THREE.CompressedArrayTexture[] } }).surfaces.arrays[0];
  const slice = albedo.mipmaps[1].data as Uint8Array, level = 16 * 16;
  expect(slice[level * 1]).toBe((4 * 16 + 1) & 255); // layer 1 (granite), mip 1
  expect(albedo.image.depth).toBe(16); expect(albedo.repeat.toArray()).toEqual([0.5, 0.5]); // room for the library
  expect(uploads).toContain(albedo);

  // A second tile with its own texture objects for the same assets adds no layer and shares the program.
  const b = tile();
  styler.apply(b.model);
  const other = b.root.children.find(child => child.name === 'surface-array') as THREE.Mesh;
  expect(other.material).toBe(material);
  expect((styler as unknown as { surfaces: { layers: number } }).surfaces.layers).toBe(5);

  styler.release(a.model);
  expect(a.root.children.map(child => (child as THREE.Mesh).userData.material_key).sort())
    .toEqual(['draft_asphalt_worn', 'draft_asphalt_worn', 'draft_concrete_slabs', 'draft_fieldstone', 'draft_granite_curb', 'draft_grass', 'draft_gravel']);
  expect(a.root.children.some(child => child.name.startsWith('surface-array'))).toBe(false);
});

test('ground maps keep their CPU mips for later tiles\' arrays; a map whose mips were released keeps its own draw', async () => {
  const { styler } = world();
  const a = tile();
  const maps = a.root.children.flatMap(child => { const m = (child as THREE.Mesh).material as THREE.MeshStandardMaterial; return [m.map!, m.normalMap!, m.roughnessMap!]; });
  styler.apply(a.model); await styler.surfacesReady();
  expect(maps.every(map => map.userData.keepData === true)).toBe(true); // world/sharedAssets.ts releaseTextureData skips them
  const b = tile();
  const granite = b.root.children.find(child => child.userData.material_key === 'draft_granite_curb') as THREE.Mesh;
  const albedo = (granite.material as THREE.MeshStandardMaterial).map as THREE.CompressedTexture;
  albedo.mipmaps = albedo.mipmaps.map(mip => ({ ...mip, data: new Uint8Array(0) })); // released elsewhere first
  styler.apply(b.model);
  expect(b.root.children.filter(child => child.userData.material_key === 'draft_granite_curb')).toHaveLength(1);
  expect(b.root.children.some(child => child.name === 'surface-array')).toBe(true); // the others still merge
});

test('the merged surface material builds a WebGPU program with r186\'s CPU builder', async () => {
  const { renderer, scene, styler } = world();
  const a = tile(); styler.apply(a.model); scene.add(a.model); await styler.surfacesReady();
  const merged = a.root.children.find(child => child.name === 'surface-array') as THREE.Mesh, material = merged.material as THREE.NodeMaterial;
  type Builder = NodeBuilder & { build(): void; fragmentShader: string };
  const nodes = new NodeManager(renderer, renderer.backend) as { _createNodeBuilder(draw: object, material: THREE.Material): Builder };
  const lightsNode = renderer.lighting.getNode(scene);
  const builder = nodes._createNodeBuilder({ object: merged, material, scene, camera: new THREE.PerspectiveCamera(), lightsNode, clippingContext: null }, material);
  builder.build();
  expect(builder.fragmentShader).toMatch(/texture_2d_array/);
  expect(builder.fragmentShader.length).toBeGreaterThan(1000);
  const dry = a.root.children.find(child => child.name === 'surface-array-dry') as THREE.Mesh;
  const dryBuilder = nodes._createNodeBuilder({ object: dry, material: dry.material, scene, camera: new THREE.PerspectiveCamera(), lightsNode, clippingContext: null }, dry.material as THREE.Material);
  dryBuilder.build();
  expect(dryBuilder.fragmentShader).toMatch(/texture_2d_array/);
});

const arraysOf = (styler: MaterialStyler) => (styler as unknown as { surfaces: { arrays: THREE.CompressedArrayTexture[] } }).surfaces.arrays;
/** Both families of a resident tile sample exactly the current set (the wet one once stayed on a released set). */
function expectSampled(model: THREE.Object3D, styler: MaterialStyler) {
  const families = new Set<THREE.Material>();
  model.traverse(object => { const mesh = object as THREE.Mesh; if (mesh.isMesh && mesh.name.startsWith('surface-array')) families.add(mesh.material as THREE.Material); });
  expect(families.size).toBe(2);
  const sampled = [...modelTextures(model)].filter(t => (t as THREE.CompressedArrayTexture).isCompressedArrayTexture);
  expect(new Set(sampled)).toEqual(new Set(arraysOf(styler)));
}

test('a surface first seen later is written in place: one layer, uploaded with its change after the first upload', async () => {
  const { styler, uploads, updates } = world();
  const a = tile(); styler.apply(a.model); await styler.surfacesReady();
  const before = arraysOf(styler)[0];
  const b = new THREE.Group(), root = new THREE.Group(); b.add(root);
  root.add(surfaceMesh('draft_cobblestone', surfaceMaterial('cobbles', 19), 0), surfaceMesh('draft_asphalt_worn', surfaceMaterial('asphalt', 1), 2));
  styler.apply(b);
  expect((before.mipmaps[0].data as Uint8Array)[32 * 32 * 5]).toBe(0); // not changed before its budgeted task
  await styler.surfacesReady();
  expect(arraysOf(styler)[0]).toBe(before); // no new set, no full re-upload
  expect(uploads.filter(t => t === before)).toHaveLength(1);
  // One task per channel for layer 5 alone, each after that set's full upload; the first tile needed none.
  expect(updates.map(u => [arraysOf(styler).indexOf(u.texture as THREE.CompressedArrayTexture), u.uploaded, u.layers]))
    .toEqual([[0, true, [5]], [1, true, [5]], [2, true, [5]]]);
  expect((before.mipmaps[0].data as Uint8Array)[32 * 32 * 5]).toBe((19 * 16) & 255); // layer 5: the new cobbles
  expectSampled(a.model, styler);
});

test('growth past the capacity swaps in a complete uploaded set that every family samples; the old set is released', async () => {
  const { styler, uploads } = world();
  const a = tile(); styler.apply(a.model); await styler.surfacesReady();
  const before = arraysOf(styler)[0]; let released = false; before.addEventListener('dispose', () => { released = true; });
  const b = new THREE.Group(), root = new THREE.Group(); b.add(root);
  for (let i = 0; i < 12; i++) root.add(surfaceMesh(`draft_asphalt_${i}`, surfaceMaterial(`asphalt${i}`, 20 + i), i * 2), surfaceMesh(`draft_asphalt_${i}`, surfaceMaterial(`asphalt${i}`, 20 + i), i * 2 + 1));
  styler.apply(b);
  expect(arraysOf(styler)[0]).toBe(before); // nothing resident samples a set that is still uploading
  await styler.surfacesReady();
  const after = arraysOf(styler)[0];
  expect(after).not.toBe(before); expect(after.image.depth).toBe(32); expect(uploads).toContain(after); expect(released).toBe(true);
  expect((after.mipmaps[0].data as Uint8Array)[32 * 32 * 16]).toBe((31 * 16) & 255); // layer 16: the twelfth new surface
  expectSampled(a.model, styler);
  expect(arraysOf(styler).every(t => t.layerUpdates.size === 0)).toBe(true);
});

/** A budget that is always spent: every checkpoint waits for a frame the test releases. */
function frames(styler: MaterialStyler) {
  const waiting: (() => void)[] = [];
  let clock = 0;
  (styler as unknown as { budget: PreparationBudget }).budget = new PreparationBudget(() => (clock += 5), () => new Promise<void>(resolve => waiting.push(resolve)));
  return { waiting, async next() { waiting.shift()?.(); for (let i = 0; i < 5; i++) await Promise.resolve(); } };
}

test('async styling spreads a tile across frames and ends exactly like the synchronous path', async () => {
  const { styler } = world(), frame = frames(styler);
  const a = tile(), b = tile();
  styler.apply(b.model);
  const styled = styler.applyAsync(a.model);
  let done = false; void styled.then(() => { done = true; });
  for (let i = 0; i < 100 && !frame.waiting.length; i++) await Promise.resolve();
  expect(done).toBe(false); expect(frame.waiting.length).toBe(1); // waiting for the next frame part-way
  let count = 0;
  while (!done && count++ < 200) await frame.next();
  expect(done).toBe(true); expect(count).toBeGreaterThan(5);
  const names = (model: THREE.Object3D) => (model.children[0] as THREE.Object3D).children.map(c => `${c.name}:${((c as THREE.Mesh).material as THREE.Material).name}`);
  expect(names(a.model)).toEqual(names(b.model));
  for (const [x, y] of a.root.children.map((c, i) => [c, b.root.children[i]] as const)) {
    const [mx, my] = [(x as THREE.Mesh).material as THREE.Material, (y as THREE.Mesh).material as THREE.Material];
    if (x.name.startsWith('surface-array')) expect(mx).toBe(my); // the city-wide array materials
    else { expect(mx.type).toBe(my.type); expect(mx.userData.weatherWetness).toBe(my.userData.weatherWetness); } // per-source clones
  }
});

test('a tile released part-way through async styling is restored and styling stops', async () => {
  const { styler } = world(), frame = frames(styler);
  const a = tile();
  const originals = a.root.children.map(c => [c, (c as THREE.Mesh).material] as const);
  const styled = styler.applyAsync(a.model);
  for (let i = 0; i < 8; i++) await frame.next(); // some sources merged or styled
  styler.release(a.model);
  for (let i = 0; i < 200 && frame.waiting.length; i++) await frame.next();
  await styled;
  expect(a.root.children.some(c => c.name.startsWith('surface-array'))).toBe(false);
  for (const [mesh, material] of originals) { expect(mesh.parent).toBe(a.root); expect((mesh as THREE.Mesh).material).toBe(material); }
  expect((styler as unknown as { owned: Map<THREE.Object3D, unknown> }).owned.has(a.model)).toBe(false);
});
