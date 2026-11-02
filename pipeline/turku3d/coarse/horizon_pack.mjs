// Lossless topology packing; source surveys and retained geometry are never modified.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const { NodeIO } = require('@gltf-transform/core');
const { ALL_EXTENSIONS } = require('@gltf-transform/extensions');
const { weld, meshopt } = require('@gltf-transform/functions');
const { MeshoptEncoder } = await import(require.resolve('meshoptimizer'));
await MeshoptEncoder.ready;
const [source, output] = process.argv.slice(2);
if (!source || !output || path.resolve(source) === path.resolve(output)) throw Error('Distinct outputs required');
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.encoder': MeshoptEncoder });
const doc = await io.read(source);
for (const mesh of doc.getRoot().listMeshes()) for (const primitive of mesh.listPrimitives()) primitive.setAttribute('NORMAL', null);
await doc.transform(weld(), meshopt({ encoder: MeshoptEncoder, level: 'medium', quantizePosition: 16, quantizeColor: 8 }));
await io.write(output, doc);
console.log(JSON.stringify({ bytes: fs.statSync(output).size }));
