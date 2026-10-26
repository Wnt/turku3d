import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createHash } from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { atlasPropSigns } from './prop_atlas.mjs';

const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const dep = name => import(pathToFileURL(require.resolve(name)).href);
const { Document } = await dep('@gltf-transform/core');
const sharp = (await dep('sharp')).default;

test('shared sign prototypes receive one atlas and one UV transform', async () => {
  const doc = new Document();
  const scene = doc.createScene();
  const originals = [];
  for (const colour of ['red', 'blue']) {
    const bytes = await sharp({ create:{ width:256, height:128, channels:3, background:colour } }).png().toBuffer();
    const material = doc.createMaterial(colour).setBaseColorTexture(
      doc.createTexture(colour).setImage(bytes).setMimeType('image/png'));
    const uv = doc.createAccessor().setType('VEC2').setArray(new Float32Array([0.5, 0.5, 0.9, 0.1]));
    const prim = doc.createPrimitive().setAttribute('TEXCOORD_0', uv).setMaterial(material);
    const mesh = doc.createMesh().addPrimitive(prim);
    scene.addChild(doc.createNode().setMesh(mesh).setExtras({ material_key:'props_sign' }));
    scene.addChild(doc.createNode().setMesh(mesh).setExtras({ material_key:'props_sign' }));
    originals.push({ bytes, prim });
  }
  await atlasPropSigns(doc, sharp);
  assert.equal(originals[0].prim.getMaterial(), originals[1].prim.getMaterial());
  const keys = originals.map(x => createHash('sha256').update(x.bytes).digest('hex')).sort();
  for (const { bytes, prim } of originals) {
    const index = keys.indexOf(createHash('sha256').update(bytes).digest('hex'));
    const uv = [...prim.getAttribute('TEXCOORD_0').getArray()];
    assert.ok(Math.abs(uv[0] - (index + 0.5) / 2) < 1e-6);
    assert.ok(Math.abs(uv[2] - (index + 0.9) / 2) < 1e-6);
    assert.equal(uv[1], 0.5);
  }
});
