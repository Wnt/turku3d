// Counterfactual baseline: current asset/schema support, with only the tile hitch fixes removed.
// Run from the repository root. Builds are intentionally separate from source preparation.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
const [current, original, output] = process.argv.slice(2);
if (!output) throw new Error('Usage: baseline.mjs CURRENT_REF ORIGINAL_REF EMPTY_OUTPUT');
const root = path.resolve(output);
fs.mkdirSync(root, { recursive: true });
if (fs.readdirSync(root).length) throw new Error('Baseline output must be empty');
const git = (...args) => execFileSync('git', args, { maxBuffer: 128 * 1024 ** 2 });
execFileSync('tar', ['-xf', '-', '-C', root], { input: git('archive', current) });
const restored = ['game/src/render/compileModel.ts', 'game/src/render/post.ts', 'game/src/render/water.ts', 'game/src/world/tiles.ts'];
for (const file of restored) fs.writeFileSync(path.join(root, file), git('show', `${original}:${file}`));
const main = path.join(root, 'game/src/main.ts');
fs.writeFileSync(main, fs.readFileSync(main, 'utf8').replace('tiles.releasePreparedModel(m); ', '').replace('tiles.prepareModel(m); ', ''));
const loader = path.join(root, 'game/src/world/coarseTiles.ts');
fs.writeFileSync(loader, fs.readFileSync(loader, 'utf8').replace('    MeshoptDecoder.useWorkers(2);\n', ''));
fs.symlinkSync(path.resolve('game/node_modules'), path.join(root, 'game/node_modules'));
fs.writeFileSync(path.join(root, 'BASELINE.json'), JSON.stringify({ current: git('rev-parse', current).toString().trim(),
  original: git('rev-parse', original).toString().trim(), restored,
  edits: ['remove final shadow preparation/release callbacks', 'disable shared Meshopt decoder workers'],
  note: 'Current facade/schema support and draft request ordering retained; counterfactual renderer baseline, not an historical release.' }, null, 2));
console.log(root);
