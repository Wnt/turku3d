import assert from 'node:assert/strict';
import { test } from 'node:test';
import { atlasUv, sourceUvInside } from './facade_atlas.mjs';

test('source UV edge tolerance is limited to one source pixel', () => {
  const size = [2048, 1024];
  assert.equal(sourceUvInside(0.003, -0.00022, size), true);
  assert.equal(sourceUvInside(0.003, -0.002, size), false);
  assert.equal(sourceUvInside(1.002, 0.5, size), false);
  assert.equal(sourceUvInside(Number.NaN, 0.5, size), false);
});

test('a two-metre door stays at ground level in every atlas row', () => {
  const wallHeight = 10;
  const scale = [1 / 8, -1 / wallHeight];
  const offset = [0, 1];
  const pageHeight = 1024;
  for (const y of [0, 512]) {
    const entry = { x: 128, y, width: 256, height: 400 };
    const base = atlasUv(4, 0, scale, offset, entry, pageHeight);
    const doorTop = atlasUv(4, 2, scale, offset, entry, pageHeight);
    const eaves = atlasUv(4, wallHeight, scale, offset, entry, pageHeight);
    assert.equal(base[1], (y + 4 + 400) / pageHeight);
    assert.equal(doorTop[1], (y + 4 + 320) / pageHeight);
    assert.equal(eaves[1], (y + 4) / pageHeight);
    assert.ok(base[1] > doorTop[1] && doorTop[1] > eaves[1]);
    assert.equal(base[0], doorTop[0]);
  }
});

// Exercise the actual atlas layout and cache with lossless PNG codec stand-ins.
// The expensive external codec itself is measured on real OCI assets after deploy.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { atlasFacades } from './facade_atlas.mjs';
const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const dep = name => import(pathToFileURL(require.resolve(name)).href);
const { Document } = await dep('@gltf-transform/core');
const { KHRTextureBasisu, KHRTextureTransform } = await dep('@gltf-transform/extensions');
const sharp = (await dep('sharp')).default;

function atlasFixture(change = 0) {
  const doc = new Document(), buffer = doc.createBuffer(), scene = doc.createScene();
  for (let wall = 0; wall < 2; wall++) {
    const maps = ['albedo', 'normal', 'orm'].map((slot, i) => {
      const bytes = Buffer.alloc(32);
      bytes.writeUInt32LE(64, 20); bytes.writeUInt32LE(64, 24);
      bytes[28] = wall * 60 + i * 10 + (wall === 0 && i === 0 ? change : 0);
      return doc.createTexture().setImage(bytes).setMimeType('image/ktx2')
        .setExtras({ facade_map:{ id:`wall${wall}`, slot, width:1, height:1 } });
    });
    const mat = doc.createMaterial().setBaseColorTexture(maps[0]).setNormalTexture(maps[1])
      .setOcclusionTexture(maps[2]).setMetallicRoughnessTexture(maps[2]);
    mat.getBaseColorTextureInfo().setExtension('KHR_texture_transform',
      doc.createExtension(KHRTextureTransform).createTransform().setScale([1, 1]).setOffset([0, 0]));
    const uv = doc.createAccessor().setType('VEC2').setBuffer(buffer).setArray(new Float32Array([0, 0, 1, 0, 0, 1]));
    scene.addChild(doc.createNode().setMesh(doc.createMesh().addPrimitive(doc.createPrimitive()
      .setMaterial(mat).setAttribute('TEXCOORD_0', uv))));
  }
  return doc;
}

test('atlas recipe hits skip every decode and composite across tiles; only changed slot rebuilds', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'atlas-cache-'));
  const request = tile => ({ textureCache:path.join(root, tile), sharedTextureCache:path.join(root, 'shared') });
  const calls = [];
  const decode = async bytes => {
    calls.push(['decode', 'extract']);
    const value = bytes[28];
    const data = await sharp({ create:{ width:64, height:64, channels:3, background:{ r:value, g:50, b:100 } } }).raw().toBuffer();
    return { data, width:64, height:64 };
  };
  const run = async (tool, args) => {
    calls.push([tool, args[0]]);
    if (tool === 'toktx') fs.copyFileSync(args.at(-1), args.at(-2));
  };
  try {
    const first = atlasFixture();
    assert.deepEqual(await atlasFacades(first, request('a'), { sharp, run, KHRTextureBasisu, decode }), { pages:1, facades:2 });
    assert.equal(calls.filter(([tool]) => tool === 'toktx').length, 3);
    assert.equal(calls.filter(([, op]) => op === 'extract').length, 6);
    const packed = doc => doc.getRoot().listTextures().filter(t => t.getName().startsWith('facade_atlas')).map(t => t.getImage());
    const uv = doc => doc.getRoot().listNodes().map(n => Array.from(n.getMesh().listPrimitives()[0].getAttribute('TEXCOORD_0').getArray()));
    const cached = atlasFixture();
    await atlasFacades(cached, request('b'), {
      sharp:() => { throw new Error('cache hit composited'); },
      run:() => { throw new Error('cache hit ran codec'); }, KHRTextureBasisu,
    });
    assert.deepEqual(packed(cached), packed(first));
    assert.deepEqual(uv(cached), uv(first));
    calls.length = 0;
    const changed = atlasFixture(1);
    await atlasFacades(changed, request('c'), { sharp, run, KHRTextureBasisu, decode });
    assert.equal(calls.filter(([tool]) => tool === 'toktx').length, 1);
    assert.equal(calls.filter(([, op]) => op === 'extract').length, 1);
    assert.deepEqual(packed(changed).slice(1), packed(first).slice(1));
    assert.deepEqual(uv(changed), uv(first));
  } finally { fs.rmSync(root, { recursive:true, force:true }); }
});
