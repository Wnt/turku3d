#!/usr/bin/env node
// Grandstand roofs of a built tile, from the visual GLB alone (no GPU): for every
// venue:<building> stand, the roof slab's height range and its clearance over the top seat row.
// Usage: node scripts/dev/stand_roof_audit.mjs TILE_DIR [TILE_DIR...]   (needs TURKU3D_GLTF_TOOLS)
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';

const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const { NodeIO } = require('@gltf-transform/core');
const { ALL_EXTENSIONS } = require('@gltf-transform/extensions');
const { MeshoptDecoder } = await import(require.resolve('meshoptimizer'));
await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.decoder': MeshoptDecoder });

function heights(prim, matrix) {
  const position = prim.getAttribute('POSITION');
  const out = [];
  const p = [0, 0, 0];
  for (let i = 0; i < position.getCount(); i++) {
    position.getElement(i, p);
    out.push(matrix[1] * p[0] + matrix[5] * p[1] + matrix[9] * p[2] + matrix[13]);
  }
  return out;
}

const all = process.argv.includes('--all');  // every material's extent, for diagnosis
for (const dir of process.argv.slice(2).filter(a => a !== '--all')) {
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json')));
  const tile = manifest.tile;
  const file = path.join(dir, manifest.visual[tile].url);
  const raw = fs.readFileSync(file), length = raw.readUInt32LE(12);
  const json = JSON.parse(raw.subarray(20, 20 + length).toString());
  const resources = { '@glb.bin': raw.subarray(28 + length) };
  if (json.buffers?.[0] && !json.buffers[0].uri) json.buffers[0].uri = '@glb.bin';
  for (const image of json.images ?? []) if (image.uri) resources[image.uri] = new Uint8Array(4);
  const doc = await io.readJSON({ json, resources });
  const stands = {};
  for (const node of doc.getRoot().listNodes()) {
    if (!node.getMesh() || node.getExtension('EXT_mesh_gpu_instancing')) continue;
    const matrix = node.getWorldMatrix();
    for (const prim of node.getMesh().listPrimitives()) {
      const name = prim.getMaterial()?.getName() ?? '';
      if (all) {
        const P = prim.getAttribute('POSITION'), q = [0, 0, 0];
        const e = stands[name] ??= { ymin: Infinity, ymax: -Infinity, xmin: Infinity, xmax: -Infinity, zmin: Infinity, zmax: -Infinity };
        for (let i = 0; i < P.getCount(); i++) {
          P.getElement(i, q);
          const x = matrix[0] * q[0] + matrix[4] * q[1] + matrix[8] * q[2] + matrix[12];
          const y = matrix[1] * q[0] + matrix[5] * q[1] + matrix[9] * q[2] + matrix[13];
          const z = matrix[2] * q[0] + matrix[6] * q[1] + matrix[10] * q[2] + matrix[14];
          e.ymin = Math.min(e.ymin, y); e.ymax = Math.max(e.ymax, y); e.xmin = Math.min(e.xmin, x);
          e.xmax = Math.max(e.xmax, x); e.zmin = Math.min(e.zmin, z); e.zmax = Math.max(e.zmax, z);
        }
        continue;
      }
      const match = /^venue:([^:]+):(roof|seat):/.exec(name);
      if (!match) continue;
      const ys = heights(prim, matrix);
      const s = stands[match[1]] ??= {};
      const kind = s[match[2]] ??= { min: Infinity, max: -Infinity, vertices: 0 };
      for (const y of ys) { kind.min = Math.min(kind.min, y); kind.max = Math.max(kind.max, y); }
      kind.vertices += ys.length;
    }
  }
  if (all) {
    for (const [name, e] of Object.entries(stands).sort((a, b) => b[1].ymax - a[1].ymax))
      process.stdout.write([name.padEnd(56), ...Object.values(e).map(v => v.toFixed(1))].join(' ') + '\n');
    continue;
  }
  for (const [building, s] of Object.entries(stands)) {
    const r = (x) => Math.round(x * 100) / 100;
    process.stdout.write(JSON.stringify({
      tile, building, visual: manifest.visual[tile].sha256.slice(0, 12),
      seat_top: s.seat && r(s.seat.max), roof_min: s.roof && r(s.roof.min), roof_max: s.roof && r(s.roof.max),
      roof_over_top_row: s.roof && s.seat ? r(s.roof.max - s.seat.max) : null,
      roof_rise: s.roof ? r(s.roof.max - s.roof.min) : null,
    }) + '\n');
  }
}
