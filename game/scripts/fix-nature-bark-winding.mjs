#!/usr/bin/env node
// Selectively repair the shipped nature library's inward procedural bark indices.
// Full rebuilds also use trees.mjs's corrected tube winding. Textures, attributes and other oriented faces stay unchanged (the meshopt codec may cyclically rotate triplets).
//   node scripts/fix-nature-bark-winding.mjs --check
//   node scripts/fix-nature-bark-winding.mjs [--out DIR]
import './nature/node-version.mjs';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { MeshoptEncoder, MeshoptDecoder } from 'meshoptimizer';

const args = process.argv.slice(2), root = path.resolve(import.meta.dirname, '../public/assets/nature');
const at = args.indexOf('--out'), out = at < 0 ? root : path.resolve(args[at + 1]);
await MeshoptDecoder.ready; await MeshoptEncoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder });
const file = path.join(root, 'nature.glb'), doc = await io.read(file);

/** Winding relative to the authored smooth normal; degenerate triangles do not vote. */
function winding(prim) {
  const P = prim.getAttribute('POSITION'), N = prim.getAttribute('NORMAL'), I = prim.getIndices();
  if (!P || !N || !I) return null;
  assert.equal(I.getCount() % 3, 0, 'Triangle index count');
  const p = [[], [], []], n = [[], [], []]; let front = 0, back = 0, degenerate = 0;
  for (let i = 0; i < I.getCount(); i += 3) {
    for (let k = 0; k < 3; k++) {
      const j = I.getScalar(i + k);
      assert(j >= 0 && j < P.getCount() && j < N.getCount(), 'Index within vertex arrays');
      P.getElement(j, p[k]); N.getElement(j, n[k]);
      assert([...p[k], ...n[k]].every(Number.isFinite) && Math.hypot(...n[k]) > 0, 'Finite vertices and nonzero normals');
    }
    const u = p[1].map((x,k) => x-p[0][k]), v = p[2].map((x,k) => x-p[0][k]);
    const cross = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]];
    if (Math.hypot(...cross) < 1e-16) { degenerate++; continue; }
    const dot = cross.reduce((s,x,k)=>s+x*(n[0][k]+n[1][k]+n[2][k]),0);
    if (dot >= 0) front++; else back++;
  }
  return { front, back, degenerate };
}
const rows = [];
for (const node of doc.getRoot().listNodes()) for (const prim of node.getMesh()?.listPrimitives() ?? []) {
  const name = prim.getMaterial()?.getName();
  if (!['nature_bark', 'nature_leaves'].includes(name)) continue;
  rows.push({ node:node.getName(), material:name, ...winding(prim) });
}
console.log(JSON.stringify(rows,null,2));
if (args.includes('--check')) process.exit(rows.some(r => r.material === 'nature_bark' && r.back > 0) ? 1 : 0);
const changed = new Set();
for (const mesh of doc.getRoot().listMeshes()) for (const prim of mesh.listPrimitives()) {
  if (prim.getMaterial()?.getName() !== 'nature_bark') continue;
  const vote = winding(prim);
  assert(vote && vote.front + vote.back > 0, 'Nonempty bark surface');
  assert(!(vote.front && vote.back), 'Mixed bark winding requires manual investigation');
  if (!vote.back) continue;
  const index = prim.getIndices();
  for (const parent of index.listParents()) {
    assert(typeof parent.getMaterial !== 'function' || parent.getMaterial()?.getName() === 'nature_bark',
      'Bark indices must not be shared with non-bark geometry');
  }
  if (changed.has(index)) continue;
  const a = index.getArray().slice();
  for (let i=0;i<a.length;i+=3) [a[i+1],a[i+2]]=[a[i+2],a[i+1]];
  index.setArray(a); changed.add(index);
}
if (!changed.size) { console.log('Bark winding already faces outward; nothing rewritten.'); process.exit(0); }
fs.mkdirSync(out,{recursive:true});
const dest=path.join(out,'nature.glb');await io.write(dest,doc);
const written = await io.read(dest);
for (const mesh of written.getRoot().listMeshes()) for (const prim of mesh.listPrimitives()) {
  if (prim.getMaterial()?.getName() !== 'nature_bark') continue;
  const vote = winding(prim);
  assert(vote && vote.front > 0 && vote.back === 0, 'Serialized bark must face outward');
}
const glb=fs.readFileSync(dest), manifest=JSON.parse(fs.readFileSync(path.join(root,'nature.json'),'utf8'));
manifest.glb={bytes:glb.length,sha256:createHash('sha256').update(glb).digest('hex')};
fs.writeFileSync(path.join(out,'nature.json'),JSON.stringify(manifest,null,1)+'\n');
console.log(JSON.stringify({repairedIndexAccessors:changed.size,...manifest.glb}));
