// Advisory verification of a frozen publication, outside every producer worker.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { deserialize } from 'node:v8';
import { auditVisual } from './visual_audit.mjs';
import { snapshot, compareSnapshots } from './visual_snapshot.mjs';

const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS, 'package.json'));
const dep = name => import(pathToFileURL(require.resolve(name)).href);
const { NodeIO } = await dep('@gltf-transform/core');
const { ALL_EXTENSIONS } = await dep('@gltf-transform/extensions');
const { MeshoptDecoder } = await dep('meshoptimizer');
await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.decoder': MeshoptDecoder });
const [file, expected] = process.argv.slice(2);
const started = performance.now();
const doc = await io.read(file);
const decoded = performance.now();
const wall = auditVisual(doc);
const audited = performance.now();
const error = expected ? compareSnapshots(deserialize(fs.readFileSync(expected)), snapshot(doc)) : null;
process.stdout.write(JSON.stringify({ wall_surfaces:wall, max_error_m:error, seconds:{
  decode:(decoded-started)/1000, walls:(audited-decoded)/1000,
  snapshot:(performance.now()-audited)/1000, total:(performance.now()-started)/1000,
} }));
