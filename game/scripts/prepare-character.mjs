// CC0 MakeHuman outfit retargeted into the existing CC0 Quaternius UAL bind pose.
// Usage: npm run prepare-character -- <source-directory> <UAL.glb> <output.glb>
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS, EXTMeshoptCompression, KHRTextureBasisu } from '@gltf-transform/extensions';
import { prune, dedup, resample, unpartition, weld } from '@gltf-transform/functions';
import { MeshoptEncoder } from 'meshoptimizer';
import { BODY_ZONES, MORPHS, addOutfit, loadRig } from './lib/character-outfit.mjs';

const [sourceDir, animationFile, output] = process.argv.slice(2);
if (!sourceDir || !animationFile || !output) throw new Error('usage: prepare-character.mjs <source-dir> <UAL.glb> <output.glb>');
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.encoder': MeshoptEncoder });
const doc = await loadRig(io, animationFile, new Set(['Idle_Loop', 'Walk_Loop', 'Walk_Formal_Loop', 'Jog_Fwd_Loop', 'Sprint_Loop', 'Driving_Loop', 'Jump_Start', 'Jump_Loop', 'Jump_Land']));
const root = doc.getRoot(), buffer = root.listBuffers()[0];
const { sources, triangles } = await addOutfit(io, doc, { sourceDir,
  parts: ['base_body', 'clothing_hoodie', 'clothing_jeans', 'clothing_shoes', 'hair_short02'],
  hidden: BODY_ZONES.feet | BODY_ZONES.legs | BODY_ZONES.hoodie, morphs: [[MORPHS.male, 0.65]] });
for (const material of root.listMaterials()) {
  material.setExtras({});
  if (material.getName() === 'Cloth_hoodie') material.setBaseColorFactor([0.12, 0.19, 0.14, 1]);
  if (material.getName() === 'Cloth_jeans') material.setBaseColorFactor([0.10, 0.15, 0.22, 1]);
}
await doc.transform(weld({ overwrite: true }), prune(), dedup(), resample(), unpartition());
const cache = path.join(sourceDir, 'ktx2'); fs.mkdirSync(cache, { recursive: true });
for (const texture of root.listTextures()) {
  const srgb = root.listMaterials().some(m => m.getBaseColorTexture() === texture || m.getEmissiveTexture() === texture);
  const hash = createHash('sha256').update(texture.getImage()).update(String(srgb)).digest('hex');
  const dest = path.join(cache, `${hash}.ktx2`);
  if (!fs.existsSync(dest)) {
    const input = path.join(cache, `${hash}.png`); fs.writeFileSync(input, texture.getImage());
    const args = ['--t2', '--genmipmap', '--threads', '2', '--assign_oetf', srgb ? 'srgb' : 'linear', '--assign_primaries', 'bt709', '--encode', srgb ? 'etc1s' : 'uastc', ...(srgb ? ['--qlevel', '180'] : ['--uastc_quality', '2', '--zcmp', '18']), dest, input];
    const result = spawnSync('toktx', args, { encoding: 'utf8' });
    if (result.status !== 0) throw new Error(`toktx: ${result.error ?? result.stderr}`);
  }
  texture.setImage(fs.readFileSync(dest)).setMimeType('image/ktx2');
}
doc.createExtension(KHRTextureBasisu).setRequired(true);
await MeshoptEncoder.ready;
doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.FILTER });
// All accessors share one buffer after the copied source documents are pruned.
for (const accessor of root.listAccessors()) accessor.setBuffer(buffer);
await io.write(output, doc);
console.log(JSON.stringify({ triangles, bytes: fs.statSync(output).size, clips: root.listAnimations().map(a => a.getName()), sources }, null, 2));
