// Pack original Blender fleet LODs. No reference textures may enter the runtime asset.
// node scripts/prepare-fleet.mjs <source-directory> <destination-directory>
import fs from 'node:fs';
import path from 'node:path';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS, EXTMeshoptCompression } from '@gltf-transform/extensions';
import { weld, prune } from '@gltf-transform/functions';
import { MeshoptEncoder } from 'meshoptimizer';
const [source, destination] = process.argv.slice(2);
if (!source || !destination) throw Error('Expected source and destination directories');
fs.mkdirSync(destination, { recursive: true });
await MeshoptEncoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.encoder': MeshoptEncoder });
for (let lod = 0; lod < 3; lod++) {
  const doc = await io.read(path.join(source, `estate_lod${lod}.glb`));
  if (doc.getRoot().listTextures().length) throw Error('Fleet export must exclude reference images');
  // Equal PBR values may have distinct semantic roles (left/right indicators).
  // Do not deduplicate materials by appearance.
  await doc.transform(weld(), prune());
  doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.FILTER });
  const output = path.join(destination, `estate_lod${lod}.glb`);
  await io.write(output, doc);
  const triangles = doc.getRoot().listMeshes().reduce((n, m) => n + m.listPrimitives().reduce((s, p) => s + (p.getIndices()?.getCount() ?? p.getAttribute('POSITION').getCount()) / 3, 0), 0);
  console.log(JSON.stringify({ lod, triangles, bytes: fs.statSync(output).size }));
}
