#!/usr/bin/env node
// Downloads the CC0 source scans of the nature library into --src (default ~/turku3d/data/assets-src/nature), checks
// their SHA-256 and keeps only the maps the build reads. Raw sources never enter git.
//   node scripts/nature/fetch-sources.mjs [--src DIR]
// The fence kit (blender/fence-kit.glb) is modelled in Blender through MCP; see public/assets/nature/README.md.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';

const args = process.argv.slice(2);
const i = args.indexOf('--src');
const src = path.resolve(i >= 0 ? args[i + 1] : path.join(os.homedir(), 'turku3d/data/assets-src/nature'));

/** ambientCG (https://ambientcg.com/view?id=ID), CC0 1.0: archive and SHA-256 as downloaded on 2026-11-02. */
const AMBIENTCG = {
  'LeafSet002_2K-PNG': '69108f3017fa1fdf087475e2f6434cd7ea3b8a7a1e962eced9ec774099a175b1',
  'LeafSet004_2K-PNG': '5d4a18c6b9247a62d7c94438b21b50e62f763c7f7d92e4dad392473b1834883d',
  'LeafSet005_2K-PNG': '48f121cfc773ba5ed74c5716204c95ff77fe3dac03cbe5267dea5a73c7b27cb2',
  'LeafSet010_2K-PNG': 'b6cdc55cbd71e0620fac637affa014dc22c079c4a7affc61fafa81d29d077c64',
  'LeafSet013_2K-PNG': '51ab47b0e2bb8070ea26fc14e1e47ec3b9a869907302f766c032c0f6583b2752',
  'LeafSet016_2K-PNG': '40da514bd1984c1ef9987e31aa3d07e014e8230ec97819ef5e99aacb54fce70f',
  'LeafSet019_2K-PNG': 'cfa0c7bdac7deae2de62581b0ad8b097663f43a606578ab2c6698ff58757125f',
  'LeafSet020_2K-PNG': 'f7fc4c0b860d75c3f0c3b94887e28fac6c8ebaa3ea6fbe031ebaa6a0c1050337',
  'LeafSet023_2K-PNG': '1de84f71c53220ea5e009dfb9636347f87cc100c11d06d552e5f16ce458a73d0',
  'LeafSet024_2K-PNG': 'afc5e98c728d8550bc609218eb7b92c3e5aa47c4d449337efc479d1b95f55f06',
  'PineNeedles001_2K-PNG': '853b5b1e308969867407426fe7299797fd27ec488c75a005b7fd467b2ba822af',
  'Foliage001_2K-PNG': '32121763ebc2a1adca7ea64dbeb20ee759ea101bc79f6a066cbc2d39ae1dfdaa',
  'Foliage003_2K-PNG': '67c9a94a7ef14eb2aee2f36b04407501e6914ea8812ede9cbc4fc2d615427575',
  'Foliage006_2K-PNG': '1c9682dfcc07c9417745073f3c3641ad2bf02bc1a7b8b27098ddc8652555fd57',
  'Bark002_1K-JPG': 'd4b0363d39df7923d6c93deec9772763b7d1512623f66d07380d4142d4825b26',
  'Bark004_1K-JPG': '6161a463afae25f69e058356d8b6e0bd1498b10f2949ea90880e25a031063b44',
  'Bark009_1K-JPG': 'cda06f2bf1d92a5d51806ed9128a6adbf0022e9af8dda397dd2952d920fddb11',
  'Bark010_1K-JPG': '3cd7dbca3af9d09e549cef33a550782c2b9b7944ccdb4ed3d9e3c9f3e4cdeb82',
  'Granite003B_1K-JPG': '6087627a56bea42176f6053787674957a3b82898e72dfd2332ed2e9dcc4fcc5a',
  'PaintedWood008A_1K-JPG': '1f6b84943a05c8c40e668ba4c82274bf6a55deae69264ea28139bcf38dac1921',
};
/** Poly Haven (https://polyhaven.com/a/ID), CC0 1.0: 1k diffuse and OpenGL normal maps. */
const POLYHAVEN = {
  'pine_bark/pine_bark_diff_1k.jpg': '89ddfbff0388d30caff902ba600cdf75eb1e651ca71ece6594b5a9b27abb47ab',
  'pine_bark/pine_bark_nor_gl_1k.jpg': '666e5f175a010f12577687dd8029125bff99110e6a97958afb295b34266437db',
  'bark_willow/bark_willow_diff_1k.jpg': 'c7de3072c2320934a3d9f5bc6c9cb58819952c0b3bd04f4e71b24cf89dbb6ad6',
  'bark_willow/bark_willow_nor_gl_1k.jpg': '9d5a771315a85c2d890526af9826843e7967ed3330da49bef7e86c3e5a6c0b6a',
  'bark_brown_02/bark_brown_02_diff_1k.jpg': '920fa0bed0c9d78c1d530e99795113afd532d7db746de440f4a85d7c83ed0f1a',
  'bark_brown_02/bark_brown_02_nor_gl_1k.jpg': '0d5e691e8ad8bcd093a3587887fda2a6b4a948b5708a4f256f7919584c6eb857',
  'jolcham_oak_bark_01/jolcham_oak_bark_01_diff_1k.jpg': '08c26b3ea38718dfc3902d0889ac06e2ce0ae9cddb31d8988723eee1780a71c8',
  'jolcham_oak_bark_01/jolcham_oak_bark_01_nor_gl_1k.jpg': 'bdc5a310b183fdb3dee4c2c8871b15f57f2fbbaf68253a73303d8320976b1826',
};

const sha256 = data => createHash('sha256').update(data).digest('hex');
async function download(url, expected) {
  const response = await fetch(url, { headers: { 'User-Agent': 'curl/8.7.1' } });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  const data = Buffer.from(await response.arrayBuffer());
  if (sha256(data) !== expected) throw new Error(`${url}: SHA-256 ${sha256(data)} != ${expected}`);
  return data;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  for (const [file, hash] of Object.entries(AMBIENTCG)) {
    const id = file.split('_')[0], dir = path.join(src, 'acg', id);
    if (fs.existsSync(dir) && fs.readdirSync(dir).some(f => f.includes('_Color.'))) continue;
    fs.mkdirSync(dir, { recursive: true });
    const zip = path.join(src, 'acg', `${file}.zip`);
    fs.writeFileSync(zip, await download(`https://ambientcg.com/get?file=${file}.zip`, hash));
    const r = spawnSync('unzip', ['-o', '-q', zip, '*_Color.*', '*_Opacity.*', '*_NormalGL.*', '-d', dir], { encoding: 'utf8' });
    if (r.status !== 0 && r.status !== 11) throw new Error(`unzip ${zip}: ${r.stderr}`);
    fs.rmSync(zip);
    console.log(`ambientCG ${file}`);
  }
  for (const [file, hash] of Object.entries(POLYHAVEN)) {
    const dest = path.join(src, 'polyhaven', file);
    if (fs.existsSync(dest)) continue;
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.writeFileSync(dest, await download(`https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k/${file}`, hash));
    console.log(`Poly Haven ${file}`);
  }
}
