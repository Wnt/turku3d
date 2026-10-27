// Bake MiniPoly's CC BY 3.0 "Scooter" (poly.pizza 3vLV1QtLFP) into the physics frame of SCOOTER:
// +X forward, +Y up, tyre contact at y = 0, wheel contacts on the physics wheelbase. Drops the
// metal/roughness texture (flat matte palette like the Kenney cars) and keeps base colour and lamp emission.
// Usage: node scripts/prepare-scooter.mjs public/assets/vehicles/minipoly-scooter-source.glb public/assets/vehicles/scooter.glb
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { prune, transformMesh } from '@gltf-transform/functions';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';

const [source, destination] = process.argv.slice(2);
if (!source || !destination) throw new Error('Usage: prepare-scooter.mjs <source scooter.glb> <output.glb>');
// Physics wheel contacts (src/physics/constants.ts SCOOTER.wheels[0..1].x).
const FRONT_X = 0.785, REAR_X = -0.465;

const io = new NodeIO().registerExtensions(ALL_EXTENSIONS);
const doc = await io.read(source);
const root = doc.getRoot();
const scene = root.getDefaultScene() ?? root.listScenes()[0];
const nodes = root.listNodes().filter(n => n.getMesh());
if (nodes.length !== 1) throw new Error(`expected one mesh node, found ${nodes.length}`);
const [node] = nodes;
const mesh = node.getMesh();

// Column-major 4x4 helpers.
const mul = (a, b) => {
  const out = new Array(16).fill(0);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) for (let k = 0; k < 4; k++) out[c * 4 + r] += a[k * 4 + r] * b[c * 4 + k];
  return out;
};
const apply = (m, p) => [0, 1, 2].map(r => m[r] * p[0] + m[4 + r] * p[1] + m[8 + r] * p[2] + m[12 + r]);
// Source forward is +Z after its node transform; a +90 degree turn about Y maps +Z to +X.
const yaw = [0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1];
const oriented = mul(yaw, node.getWorldMatrix());

const points = [];
for (const prim of mesh.listPrimitives()) {
  const pos = prim.getAttribute('POSITION');
  for (let i = 0; i < pos.getCount(); i++) points.push(apply(oriented, pos.getElement(i, [])));
}
const ground = Math.min(...points.map(p => p[1]));
const low = points.filter(p => p[1] < ground + 0.03);
const mean = values => values.reduce((a, b) => a + b, 0) / values.length;
const front = mean(low.filter(p => p[0] > 0).map(p => p[0])), rear = mean(low.filter(p => p[0] < 0).map(p => p[0]));
const s = (FRONT_X - REAR_X) / (front - rear);
const fit = [s, 0, 0, 0, 0, s, 0, 0, 0, 0, s, 0, FRONT_X - front * s, -ground * s, 0, 1];
transformMesh(mesh, mul(fit, oriented));
node.setTranslation([0, 0, 0]).setRotation([0, 0, 0, 1]).setScale([1, 1, 1]).setName('scooter');
for (const child of scene.listChildren()) if (child !== node) { child.removeChild(node); scene.removeChild(child); }
scene.addChild(node);

for (const material of root.listMaterials()) {
  material.setMetallicRoughnessTexture(null).setMetallicFactor(0).setRoughnessFactor(0.75);
}
await doc.transform(prune());
await io.write(destination, doc);
const bytes = await readFile(destination);
console.log(`${destination}: scale ${s.toFixed(4)}, ${bytes.length} bytes, sha256 ${createHash('sha256').update(bytes).digest('hex')}`);
