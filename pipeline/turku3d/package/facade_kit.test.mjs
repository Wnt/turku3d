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
