import assert from 'node:assert/strict';
import { test } from 'node:test';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { splitColourSpaces } from './colour_spaces.mjs';

const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const { Document, NodeIO } = require('@gltf-transform/core');
const { KHRTextureTransform } = require('@gltf-transform/extensions');
const { dedup, listTextureSlots } = require('@gltf-transform/functions');

// Two valid 1x1 PNGs (dedup reads image sizes).
const PNG_A = new Uint8Array(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64'));
const PNG_B = new Uint8Array(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64'));

const mixed = doc => doc.getRoot().listTextures().filter(t => {
  const slots = listTextureSlots(t);
  return slots.some(s => s === 'baseColorTexture' || s === 'emissiveTexture')
    && slots.some(s => s !== 'baseColorTexture' && s !== 'emissiveTexture');
});

test('a texture deduplicated into colour and data slots is split, one copy per colour space', async () => {
  const doc = new Document(), bytes = PNG_A;
  doc.createBuffer();
  const transform = doc.createExtension(KHRTextureTransform);
  const image = name => doc.createTexture(name).setImage(bytes.slice()).setMimeType('image/png');
  const wall = doc.createMaterial('wall').setBaseColorTexture(image('wall_albedo'));
  const orm = image('kerb_orm');
  const kerb = doc.createMaterial('kerb').setOcclusionTexture(orm).setMetallicRoughnessTexture(orm)
    .setBaseColorTexture(doc.createTexture('kerb_albedo').setImage(PNG_B.slice()).setMimeType('image/png'));
  kerb.getOcclusionTextureInfo().setTexCoord(1).setWrapS(33071)
    .setExtension('KHR_texture_transform', transform.createTransform().setScale([2, 3]));
  await doc.transform(dedup({ propertyTypes: ['Texture', 'Material'] }));
  assert.equal(mixed(doc).length, 1, 'identical bytes merge into one texture in both kinds of slot');
  const merged = wall.getBaseColorTexture();

  const splits = splitColourSpaces(doc);
  assert.equal(mixed(doc).length, 0);
  assert.deepEqual(splits.map(s => s.materials), [['kerb']]);
  assert.deepEqual(splits[0].slots, ['baseColorTexture', 'metallicRoughnessTexture', 'occlusionTexture']);
  assert.equal(wall.getBaseColorTexture(), merged, 'the colour slot keeps the original');
  const copy = kerb.getOcclusionTexture();
  assert.notEqual(copy, merged);
  assert.equal(kerb.getMetallicRoughnessTexture(), copy, 'occlusion and metal/roughness share one data copy');
  assert.deepEqual(copy.getImage(), merged.getImage());
  assert.equal(kerb.getOcclusionTextureInfo().getTexCoord(), 1, 'the slot keeps its TextureInfo');
  assert.equal(kerb.getOcclusionTextureInfo().getWrapS(), 33071);
  assert.deepEqual(kerb.getOcclusionTextureInfo().getExtension('KHR_texture_transform').getScale(), [2, 3]);

  const io = new NodeIO().registerExtensions([KHRTextureTransform]);
  const decoded = await io.readBinary(await io.writeBinary(doc));
  assert.equal(decoded.getRoot().listTextures().length, 3);
  assert.equal(mixed(decoded).length, 0);
});

test('textures in one kind of slot, and skipped textures, are left alone', () => {
  const doc = new Document();
  const t = doc.createTexture('orm').setImage(PNG_A.slice()).setMimeType('image/png');
  const k = doc.createTexture('ktx').setImage(new Uint8Array([2])).setMimeType('image/ktx2');
  doc.createMaterial('a').setOcclusionTexture(t).setMetallicRoughnessTexture(t)
    .setBaseColorTexture(k).setNormalTexture(k);
  assert.deepEqual(splitColourSpaces(doc, { skip: x => x.getMimeType() === 'image/ktx2' }), []);
  assert.equal(doc.getRoot().listTextures().length, 2);
});
