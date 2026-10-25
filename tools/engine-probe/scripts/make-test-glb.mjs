// Writes the probe level (+ a 2M-tri 25cm grid) as GLB with a PNG basecolor texture,
// to test the GPU-less asset pipeline (gltfpack / gltf-transform + KTX2) on the Linux box.
import { Document, NodeIO } from '@gltf-transform/core';
import { readFileSync } from 'node:fs';
import { buildLevel, buildBigGrid } from '../src/level.js';
const [texPath, outPath] = process.argv.slice(2);
const doc = new Document(); const buf = doc.createBuffer();
const tex = doc.createTexture('ortho').setImage(readFileSync(texPath)).setMimeType('image/png');
const mat = doc.createMaterial('ground').setBaseColorTexture(tex).setRoughnessFactor(0.9);
const scene = doc.createScene();
for (const [name, g] of [['level', buildLevel()], ['grid', buildBigGrid(1000, 0.25)]]) {
  const n = g.positions.length / 3; const uv = new Float32Array(n * 2);
  for (let i = 0; i < n; i++) { uv[2 * i] = g.positions[3 * i] / 250; uv[2 * i + 1] = g.positions[3 * i + 2] / 250; }
  const prim = doc.createPrimitive().setMaterial(mat)
    .setAttribute('POSITION', doc.createAccessor().setType('VEC3').setArray(g.positions).setBuffer(buf))
    .setAttribute('TEXCOORD_0', doc.createAccessor().setType('VEC2').setArray(uv).setBuffer(buf))
    .setIndices(doc.createAccessor().setType('SCALAR').setArray(g.indices).setBuffer(buf));
  scene.addChild(doc.createNode(name).setMesh(doc.createMesh(name).addPrimitive(prim)));
}
await new NodeIO().write(outPath, doc);
console.log('wrote', outPath);
