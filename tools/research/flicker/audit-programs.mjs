// Read existing artifacts only; count buildings with mixed per-triangle facade programs.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const { NodeIO } = require('@gltf-transform/core');
const { ALL_EXTENSIONS } = require('@gltf-transform/extensions');
const { MeshoptDecoder } = await import(require.resolve('meshoptimizer'));
await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.decoder':MeshoptDecoder});
const root = process.argv[2];
const tiles = [];
for (const name of fs.readdirSync(root).filter(n => /^t0_/.test(n)).sort()) {
  const manifest = path.join(root, name, 'manifest.json');
  if (!fs.existsSync(manifest)) continue;
  const entry = JSON.parse(fs.readFileSync(manifest)).visual?.[name];
  if (!entry) continue;
  const file = path.join(root, name, entry.url);
  const raw = fs.readFileSync(file), len = raw.readUInt32LE(12);
  const json = {json:JSON.parse(raw.subarray(20,20+len).toString()), resources:{'@glb.bin':raw.subarray(28+len)}};
  json.json.buffers[0].uri = '@glb.bin';
  for (const image of json.json.images ?? []) if (image.uri) json.resources[image.uri] = new Uint8Array(4);
  const doc = await io.readJSON(json);
  const buildings = new Set(), seen = new Set(); let mixed = 0;
  for (const node of doc.getRoot().listNodes()) for (const prim of node.getMesh()?.listPrimitives() ?? []) {
    const fields = prim.listSemantics().filter(k => k.startsWith('_KIT_'));
    if (!fields.length) continue;
    const extras = node.getExtras(), indices = prim.getIndices().getArray();
    for (let i = 0; i < indices.length; i += 3) {
      const owner = extras.objects?.find(o => i >= o.start && i < o.start+o.count) ?? extras;
      if (owner.building_id) seen.add(owner.building_id);
      if (fields.some(key => {
        const a = prim.getAttribute(key), first = a.getElement(indices[i], []);
        return [1,2].some(j => a.getElement(indices[i+j], []).some((x,k) => x !== first[k]));
      })) { mixed++; if (owner.building_id) buildings.add(owner.building_id); }
    }
  }
  tiles.push({tile:name, buildings:[...buildings].sort(), inspected_buildings:seen.size, mixed_triangles:mixed});
}
console.log(JSON.stringify({tiles_inspected:tiles.length, affected_tiles:tiles.filter(t=>t.mixed_triangles).length,
  affected_buildings:new Set(tiles.flatMap(t=>t.buildings)).size, tiles}));
