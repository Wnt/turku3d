import assert from 'node:assert/strict';
import { test } from 'node:test';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { bindFacadeKit } from './facade_kit.mjs';
import { batchVisual } from './batch.mjs';

const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const dep = name => import(pathToFileURL(require.resolve(name)).href);
const { Document } = await dep('@gltf-transform/core');
const { dedup, joinPrimitives, transformPrimitive } = await dep('@gltf-transform/functions');

test('kit styles share a batch while preserving metre UVs, styles and object ranges', async () => {
  // Both published and draft requests use this binder and batcher.
  const doc = new Document(), buffer = doc.createBuffer(), scene = doc.createScene();
  const maps = Object.fromEntries(['albedo', 'normal', 'orm'].map(slot => [slot, doc.createTexture(slot)]));
  const request = { facadeKit: { maps: { albedo:'albedo', normal:'normal', orm:'orm' } } };
  const styles = [
    { module: 6, wall:'#808080', trim:'#ffffff', bay:2, storey:3 },
    { module: 41, wall:'#ffffff', trim:'#000000', bay:4, storey:5 },
  ];
  for (const [i, style] of styles.entries()) {
    const uv = doc.createAccessor().setBuffer(buffer).setType('VEC2').setArray(new Float32Array([2, 1, 6, 1, 2, 6]));
    const pos = doc.createAccessor().setBuffer(buffer).setType('VEC3').setArray(new Float32Array([0, 0, 0, 4, 0, 0, 0, 5, 0]));
    const prim = doc.createPrimitive().setAttribute('POSITION', pos).setAttribute('TEXCOORD_0', uv);
    const material = doc.createMaterial(); prim.setMaterial(material);
    const extras = { layer:'buildings', material_key:'facade_kit', object_id:`house-${i}`, facade_kit:style };
    assert.equal(bindFacadeKit(doc, request, material, prim, extras, name => maps[name]), true);
    assert.equal(prim.getAttribute('TEXCOORD_0'), uv);
    assert.deepEqual(prim.getAttribute('_KIT_LAYOUT').getElement(0, []), [style.module, style.bay, style.storey]);
    scene.addChild(doc.createNode().setMesh(doc.createMesh().addPrimitive(prim)).setExtras(extras));
  }
  await batchVisual(doc, scene, 't0_0_0', { dedup, joinPrimitives, transformPrimitive });
  const batches = scene.listChildren().filter(n => n.getMesh());
  assert.equal(batches.length, 1);
  assert.deepEqual(batches[0].getExtras().objects.map(({ object_id, start, count }) => [object_id, start, count]),
    [['house-0', 0, 3], ['house-1', 3, 3]]);
  const prim = batches[0].getMesh().listPrimitives()[0];
  assert.deepEqual(prim.getAttribute('_KIT_LAYOUT').getElement(3, []), [41, 4, 5]);
  assert.deepEqual(prim.getAttribute('_KIT_ORIGIN').getElement(3, []), [2, 1]);
  assert.ok(Math.abs(prim.getAttribute('_KIT_WALL').getElement(0, [])[0] - 0.21586) < 0.00001);
  assert.deepEqual(prim.getAttribute('_KIT_TRIM').getElement(3, []), [0, 0, 0]);
});

test('orthophoto roof RGB survives linear conversion and batches across roof classes', async () => {
  const { bindRoofColour } = await import('./materials.mjs');
  const doc = new Document(), buffer = doc.createBuffer(), scene = doc.createScene();
  for (const [i, rgb] of [[210, 215, 215], [40, 45, 45]].entries()) {
    const position = doc.createAccessor().setBuffer(buffer).setType('VEC3').setArray(new Float32Array([0, 0, 0, 1, 0, 0, 0, 0, 1]));
    const material = doc.createMaterial().setBaseColorFactor([0.25, 0.25, 0.25, 1]);
    const prim = doc.createPrimitive().setAttribute('POSITION', position).setMaterial(material);
    bindRoofColour(doc, material, prim, rgb);
    assert.equal(material.getBaseColorTexture(), null);
    assert.deepEqual(material.getBaseColorFactor(), [1, 1, 1, 1]);
    const s = rgb[0] / 255, linear = ((s + 0.055) / 1.055) ** 2.4;
    assert.ok(Math.abs(prim.getAttribute('COLOR_0').getElement(0, [])[0] - linear) < 1e-6);
    scene.addChild(doc.createNode().setExtras({ layer:'buildings', object_id:`roof-${i}` }).setMesh(doc.createMesh().addPrimitive(prim)));
  }
  await batchVisual(doc, scene, 't0_0_0', { dedup, joinPrimitives, transformPrimitive });
  const batches = scene.listChildren().filter(n => n.getMesh());
  assert.equal(batches.length, 1);
  const colours = batches[0].getMesh().listPrimitives()[0].getAttribute('COLOR_0');
  assert.ok(colours.getElement(0, [])[0] > colours.getElement(3, [])[0] * 10);
});

test('v3 carries the mapped ground floor and one entrance bay without changing opaque mode', () => {
  const doc = new Document(), buffer = doc.createBuffer();
  const uv = doc.createAccessor().setBuffer(buffer).setType('VEC2').setArray(new Float32Array([0, 0, 18, 0, 0, 12.8]));
  const pos = doc.createAccessor().setBuffer(buffer).setType('VEC3').setArray(new Float32Array([0, 0, 0, 18, 0, 0, 0, 12.8, 0]));
  const prim = doc.createPrimitive().setAttribute('POSITION', pos).setAttribute('TEXCOORD_0', uv);
  const material = doc.createMaterial();
  // A stored description must not recompute the original grid from the shorter
  // window-bearing geometry, or turn a blank frieze back into window rows.
  const request = { facadeKit: { version:3, maps: { albedo:'albedo', normal:'normal', orm:'orm' },
    descriptions: { shop: { material:'brick', window_columns:6, ground_floor:'shops',
      window_width:.65, wall_colour:'#ffffff', trim_colour:'#ffffff', storeys:6 } } } };
  const extras = { layer:'buildings', material_key:'facade_kit', object_id:'shop',
    facade_kit: { module:2, wall:'#808080', trim:'#ffffff', bay:3, storey:3.2, ground:4, upper:8.8 / 3 } };
  bindFacadeKit(doc, request, material, prim, extras, name => doc.createTexture(name));
  const floors = prim.getAttribute('_KIT_FLOORS').getElement(0, []);
  assert.equal(floors[0], 4);
  assert.ok(Math.abs(floors[1] * 3 + floors[0] - 12.8) < 1e-6);
  assert.equal(floors[2], 3);
  assert.equal(material.getExtras().facade_kit.version, 3);
  assert.equal(material.getAlphaMode(), 'OPAQUE');
  extras.facade_kit = { ...extras.facade_kit, module:43 };
  bindFacadeKit(doc, request, material, prim, extras, name => doc.createTexture(name));
  assert.equal(prim.getAttribute('_KIT_LAYOUT').getElement(0, [])[0], 43);
});

test('program walls retain their own metric grids and patch origins within one primitive', () => {
  const doc = new Document(), buffer = doc.createBuffer();
  const pos = doc.createAccessor().setBuffer(buffer).setType('VEC3').setArray(new Float32Array(24));
  const uv = doc.createAccessor().setBuffer(buffer).setType('VEC2').setArray(new Float32Array(16));
  const prim = doc.createPrimitive().setAttribute('POSITION', pos).setAttribute('TEXCOORD_0', uv);
  prim.setIndices(doc.createAccessor().setBuffer(buffer).setType('SCALAR')
    .setArray(new Uint32Array([0,3,2,0,2,1,4,7,6,4,6,5])));
  const p = { start:0, count:4, module:1, bay:3.1, storey:2, ground:2, upper:2,
    wall:'#808080', trim:'#ffffff', base:'#333333', size:[18.6, 8], opening:[.41,.62,.2,.1],
    bands:[.4,.3,.1,.2], detail:[.1,0,0,0], glazing:[5,1,.7,0], frame:'#808080' };
  const patch = { ...p, start:4, module:0, bay:2, size:[2, 1.8], origin:[5, 7], detail:[0,0,0,1] };
  const e = { layer:'buildings', material_key:'facade_kit', building_id:'house', base_height:3,
    facade_kit:p, facade_kit_walls:[p,patch] };
  const material = doc.createMaterial();
  bindFacadeKit(doc, { facadeKit:{version:3,maps:{albedo:'a',normal:'n',orm:'o'}} }, material, prim, e,
    name => doc.createTexture(name));
  assert.equal(material.getExtras().facade_kit.version, 5);
  assert.deepEqual(prim.getAttribute('_KIT_GLAZING').getElement(4, []), [5,1,Math.fround(.7),0]);
  assert.ok(Math.abs(prim.getAttribute('_KIT_FRAME').getElement(4, [])[0] - .21586) < .00001);
  assert.deepEqual(prim.getAttribute('_KIT_EXTENT').getElement(0, []), [0,3,Math.fround(18.6),8]);
  assert.deepEqual(prim.getAttribute('_KIT_EXTENT').getElement(4, []), [5,7,2,Math.fround(1.8)]);
  assert.deepEqual(prim.getAttribute('_KIT_DETAIL').getElement(4, []), [0,0,0,1]);
  assert.equal(prim.getAttribute('TEXCOORD_0'), uv);
});
