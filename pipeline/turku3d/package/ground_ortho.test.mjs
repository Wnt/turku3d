import assert from 'node:assert/strict';
import { test } from 'node:test';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { createMaterialBinder } from './materials.mjs';

const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const dep = name => import(pathToFileURL(require.resolve(name)).href);
const { Document } = await dep('@gltf-transform/core');

const { KHRTextureBasisu, KHRTextureTransform } = await dep('@gltf-transform/extensions');

test('ground ortho uses tile UVs while library detail keeps metric UVs', () => {
  const doc = new Document();
  const buffer = doc.createBuffer();
  const position = doc.createAccessor().setBuffer(buffer).setType('VEC3')
    .setArray(new Float32Array([0, 3, 0, 250, 3, 250]));
  const metric = doc.createAccessor().setBuffer(buffer).setType('VEC2')
    .setArray(new Float32Array([500, -250, 750, -500]));
  const image = doc.createTexture().setImage(new Uint8Array([1])).setMimeType('image/png');
  const material = doc.createMaterial().setBaseColorTexture(image);
  const prim = doc.createPrimitive().setAttribute('POSITION', position)
    .setAttribute('TEXCOORD_0', metric).setMaterial(material);
  const bind = createMaterialBinder(doc, { library: '/nonexistent', facadeBindings: [] },
    { KHRTextureBasisu, KHRTextureTransform });
  bind(material, prim, { material_key: 'draft_grass', ground_ortho_size: 250 });
  assert.equal(material.getBaseColorTexture(), image);
  assert.equal(material.getBaseColorTextureInfo().getTexCoord(), 1);
  assert.deepEqual([...prim.getAttribute('TEXCOORD_1').getArray()], [0, 0, 1, 1]);
  assert.equal(prim.getAttribute('TEXCOORD_0'), metric);
  assert.equal(material.getBaseColorTextureInfo().getWrapS(), 33071);
});
