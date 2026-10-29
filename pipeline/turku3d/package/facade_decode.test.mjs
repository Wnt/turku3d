import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { decodeFacadeRgb } from './facade_decode.mjs';
const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const sharp = (await import(pathToFileURL(require.resolve('sharp')))).default;

function run(tool, args) {
  const result = spawnSync(tool, args, { encoding:'utf8' });
  assert.equal(result.status, 0, result.error?.message ?? result.stderr);
}

test('persistent decoder and raw resampling match native PNG pixels for ETC1S and UASTC', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'facade-decode-'));
  try {
    const width = 16, height = 12;
    const pixels = Buffer.from(Array.from({ length:width * height * 4 }, (_, i) => (i * 97) % 256));
    const input = path.join(root, 'input.png');
    await sharp(pixels, { raw:{ width, height, channels:4 } }).png().toFile(input);
    for (const codec of ['etc1s', 'uastc']) {
      const file = path.join(root, `${codec}.ktx2`), png = path.join(root, `${codec}.png`);
      run('toktx', ['--t2', '--threads', '1', '--genmipmap', '--encode', codec,
        '--assign_oetf', codec === 'etc1s' ? 'srgb' : 'linear',
        ...(codec === 'etc1s' ? ['--clevel', '0'] : ['--zcmp', '6']), file, input]);
      run('ktx', ['extract', '--transcode', 'rgb8', file, png]);
      const decoded = await decodeFacadeRgb(fs.readFileSync(file));
      assert.equal(decoded.width, width); assert.equal(decoded.height, height);
      assert.deepEqual(decoded.data, await sharp(png).removeAlpha().raw().toBuffer());
      const old = await sharp(png).resize(8, 8, { fit:'fill' }).png().toBuffer();
      const raw = await sharp(decoded.data, { raw:{ width, height, channels:3 } })
        .resize(8, 8, { fit:'fill' }).raw().toBuffer();
      const border = { top:4, bottom:4, left:4, right:4, extendWith:'copy' };
      const pngOverlay = await sharp(old).extend(border).png().toBuffer();
      const rawOverlay = await sharp(raw, { raw:{ width:8, height:8, channels:3 } }).extend(border).raw().toBuffer();
      const canvas = { create:{ width:24, height:24, channels:3, background:'#808080' } };
      const oldAtlas = await sharp(canvas).composite([{ input:pngOverlay, left:4, top:4 }]).png().toBuffer();
      const newAtlas = await sharp(canvas).composite([{ input:rawOverlay, raw:{ width:16, height:16, channels:3 }, left:4, top:4 }]).png().toBuffer();
      assert.deepEqual(newAtlas, oldAtlas);
    }
  } finally { fs.rmSync(root, { recursive:true, force:true }); }
});
