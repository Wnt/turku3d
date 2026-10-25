// Trims the Quaternius Universal Animation Library (CC0) Godot GLB down to the clips the game uses.
// Usage: node scripts/prepare-character.mjs <AnimationLibrary_Godot_Standard.glb> public/assets/character/mannequin.glb
// Source zip: https://opengameart.org/sites/default/files/universal_animation_librarystandard.zip (see public/assets/LICENSES.md)
import { NodeIO } from '@gltf-transform/core';
import { prune, dedup, resample } from '@gltf-transform/functions';

const KEEP = new Set(['Idle_Loop', 'Walk_Loop', 'Jog_Fwd_Loop', 'Sprint_Loop', 'Driving_Loop', 'Jump_Start', 'Jump_Loop', 'Jump_Land']);
const [src, dst] = process.argv.slice(2);
if (!src || !dst) { console.error('usage: prepare-character.mjs <in.glb> <out.glb>'); process.exit(2); }
const io = new NodeIO();
const doc = await io.read(src);
const root = doc.getRoot();
for (const a of root.listAnimations()) {
  if (KEEP.has(a.getName())) continue;
  for (const c of a.listChannels()) c.dispose();
  for (const s of a.listSamplers()) { s.dispose(); }
  a.dispose();
}
await doc.transform(resample(), prune(), dedup());
const kept = root.listAnimations().map((a) => a.getName());
await io.write(dst, doc);
// bbox of the bind-pose mesh for the README
const pos = root.listMeshes()[0].listPrimitives()[0].getAttribute('POSITION');
console.log(JSON.stringify({ kept, min: pos.getMinNormalized([]), max: pos.getMaxNormalized([]) }));
