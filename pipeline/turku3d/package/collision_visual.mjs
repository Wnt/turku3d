// Decode the shipped geometry only; image bytes are irrelevant to collider visibility.
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
for (const buffer of json.buffers ?? []) if (buffer.uri && buffer.uri !== '@glb.bin') resources[buffer.uri] = fs.readFileSync(path.resolve(path.dirname(file), buffer.uri));
for (const image of json.images ?? []) if (image.uri) resources[image.uri] = new Uint8Array(4);
const doc = await io.readJSON({ json, resources });
const materials = {
  draft_chain: 'fence', draft_chain_panel: 'fence', draft_wood: 'fence', draft_bars: 'fence',
  draft_stone: 'wall', draft_brick: 'wall', draft_hedge: 'vegetation_low',
  draft_rail: 'railing', draft_bollard: 'bollard', tree_bark: 'tree_trunk',
};
const selected = [];
for (const node of doc.getRoot().listNodes()) {
  if (!node.getMesh()) continue;
  const e = node.getExtras(), material = e.layer === 'basic' ? e.surface : materials[e.material_key];
  if (material && ['fence', 'wall', 'vegetation_low', 'railing', 'bollard', 'tree_trunk'].includes(material)) selected.push(...node.getMesh().listPrimitives().map(() => material));
  else node.setMesh(null);
}
const draws = snapshot(doc);
process.stdout.write(JSON.stringify(draws.map((draw, i) => ({ material: selected[i], ...draw }))));
