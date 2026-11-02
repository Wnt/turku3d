// Geometry-only decoding: no GPU, texture downloads, codecs or builds.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import { snapshot } from './visual_snapshot.mjs';
const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const { NodeIO } = require('@gltf-transform/core');
const { ALL_EXTENSIONS } = require('@gltf-transform/extensions');
const { MeshoptDecoder } = await import(require.resolve('meshoptimizer'));
await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.decoder': MeshoptDecoder });
const file = process.argv[2], raw = fs.readFileSync(file), length = raw.readUInt32LE(12);
const json = JSON.parse(raw.subarray(20, 20 + length).toString());
const resources = { '@glb.bin': raw.subarray(28 + length) };
if (json.buffers?.[0] && !json.buffers[0].uri) json.buffers[0].uri = '@glb.bin';
for (const b of json.buffers ?? []) if (b.uri && b.uri !== '@glb.bin') resources[b.uri] = fs.readFileSync(path.resolve(path.dirname(file), b.uri));
for (const image of json.images ?? []) if (image.uri) resources[image.uri] = new Uint8Array(4);
const doc = await io.readJSON({ json, resources });
const metadata = [];
for (const node of doc.getRoot().listNodes()) {
  if (!node.getMesh()) continue;
  // Vegetation/prop instances are outside this first road/structure audit.
  if (node.getExtension('EXT_mesh_gpu_instancing')) { node.setMesh(null); continue; }
  for (const prim of node.getMesh().listPrimitives()) metadata.push({
    name: node.getName(), ...node.getExtras(), material: prim.getMaterial()?.getName(),
    material_extras: prim.getMaterial()?.getExtras(),
  });
}
process.stdout.write(JSON.stringify(snapshot(doc).map((draw, i) => ({ ...draw, metadata: metadata[i] }))));
