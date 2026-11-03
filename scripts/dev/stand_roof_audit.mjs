#!/usr/bin/env node
// Grandstand roofs of built tiles, from the visual GLB alone (no GPU). Packaging merges identical
// materials of several stands, so every connected roof slab is one stand: its top height range
// and its clearance over the highest seat row under it.
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

function world(prim, m) {
  const position = prim.getAttribute('POSITION'), p = [0, 0, 0], out = [];
  for (let i = 0; i < position.getCount(); i++) {
    position.getElement(i, p);
    out.push([m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12], m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
              m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14]]);
  }
  return out;
}

function components(points, indices) {
  // Vertices at the same position belong together even when the packager split them.
  const parent = points.map((_, i) => i);
  const find = (i) => { while (parent[i] !== i) i = parent[i] = parent[parent[i]]; return i; };
  const join = (a, b) => { parent[find(a)] = find(b); };
  const seen = new Map();
  points.forEach((q, i) => {
    const key = q.map((v) => v.toFixed(2)).join(',');
    if (seen.has(key)) join(i, seen.get(key)); else seen.set(key, i);
  });
  for (let t = 0; t + 2 < indices.length; t += 3) { join(indices[t], indices[t + 1]); join(indices[t], indices[t + 2]); }
  const groups = new Map();
  points.forEach((q, i) => { const k = find(i); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(q); });
  return [...groups.values()];
}

function hull(points) {
  const p = [...new Map(points.map((q) => [`${q[0].toFixed(2)},${q[2].toFixed(2)}`, [q[0], q[2]]])).values()]
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [], upper = [];
  for (const q of p) { while (lower.length >= 2 && cross(lower.at(-2), lower.at(-1), q) <= 0) lower.pop(); lower.push(q); }
  for (const q of p.reverse()) { while (upper.length >= 2 && cross(upper.at(-2), upper.at(-1), q) <= 0) upper.pop(); upper.push(q); }
  return [...lower.slice(0, -1), ...upper.slice(0, -1)];
}

function inside(polygon, x, z) {
  for (let i = 0; i < polygon.length; i++) {
    const a = polygon[i], b = polygon[(i + 1) % polygon.length];
    if ((b[0] - a[0]) * (z - a[1]) - (b[1] - a[1]) * (x - a[0]) < -1e-6) return false;
  }
  return polygon.length >= 3;
}

const r = (x) => Math.round(x * 100) / 100;
const ROOF_THICK = 0.7;  // venues.ROOF_THICK: the slab's underside is this far below its top
for (const dir of process.argv.slice(2)) {
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json')));
  const tile = manifest.tile;
  const file = path.join(dir, manifest.visual[tile].url);
  const raw = fs.readFileSync(file), length = raw.readUInt32LE(12);
  const json = JSON.parse(raw.subarray(20, 20 + length).toString());
  const resources = { '@glb.bin': raw.subarray(28 + length) };
  if (json.buffers?.[0] && !json.buffers[0].uri) json.buffers[0].uri = '@glb.bin';
  for (const image of json.images ?? []) if (image.uri) resources[image.uri] = new Uint8Array(4);
  const doc = await io.readJSON({ json, resources });
  const slabs = [], seats = [];
  for (const node of doc.getRoot().listNodes()) {
    if (!node.getMesh() || node.getExtension('EXT_mesh_gpu_instancing')) continue;
    const matrix = node.getWorldMatrix();
    for (const prim of node.getMesh().listPrimitives()) {
      const kind = /^venue:[^:]+:(roof|seat):/.exec(prim.getMaterial()?.getName() ?? '')?.[1];
      if (!kind) continue;
      const points = world(prim, matrix);
      if (kind === 'seat') seats.push(...components(points, prim.getIndices()?.getArray() ?? []));
      else slabs.push(...components(points, prim.getIndices()?.getArray() ?? []));
    }
  }
  for (const slab of slabs) {
    const outline = hull(slab);
    // Whole seat blocks belong to the slab over their centre.
    const centre = (c) => [c.reduce((t, q) => t + q[0], 0) / c.length, c.reduce((t, q) => t + q[2], 0) / c.length];
    const under = seats.filter((c) => inside(outline, ...centre(c))).flat();
    const ys = slab.map((q) => q[1]), xs = slab.map((q) => q[0]), zs = slab.map((q) => q[2]);
    const top = Math.max(...ys), low = Math.min(...ys);
    const seatTop = under.length ? Math.max(...under.map((q) => q[1])) : null;
    process.stdout.write(JSON.stringify({
      tile, visual: manifest.visual[tile].sha256.slice(0, 12),
      centre: [r((Math.min(...xs) + Math.max(...xs)) / 2), r((Math.min(...zs) + Math.max(...zs)) / 2)],
      roof_low: r(low), roof_top: r(top), roof_rise: r(top - low), seat_top: seatTop && r(seatTop),
      // Top-surface clearance over the highest seat row, at the slab's lowest and highest points.
      clearance_min: seatTop === null ? null : r(low + ROOF_THICK - seatTop),
      clearance_max: seatTop === null ? null : r(top - seatTop),
    }) + '\n');
  }
}
