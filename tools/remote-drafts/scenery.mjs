// Read-only source conversion. Derived distant geometry never replaces a source tile or its textures.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const { Document, NodeIO } = require('@gltf-transform/core');
const { ALL_EXTENSIONS } = require('@gltf-transform/extensions');
const { meshopt } = require('@gltf-transform/functions');
const { MeshoptDecoder, MeshoptEncoder, MeshoptSimplifier } = await import(require.resolve('meshoptimizer'));
await Promise.all([MeshoptDecoder.ready, MeshoptEncoder.ready, MeshoptSimplifier.ready]);
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.decoder': MeshoptDecoder, 'meshopt.encoder': MeshoptEncoder });
const [source, output] = process.argv.slice(2);
if (!source || !output || path.resolve(source) === path.resolve(output)) throw new Error('Distinct source and derivative output required');
const started = performance.now(), raw = fs.readFileSync(source), length = raw.readUInt32LE(12);
const json = JSON.parse(raw.subarray(20, 20 + length).toString());
const resources = { '@glb.bin': raw.subarray(28 + length) };
if (json.buffers?.[0] && !json.buffers[0].uri) json.buffers[0].uri = '@glb.bin';
for (const buffer of json.buffers ?? []) if (buffer.uri && buffer.uri !== '@glb.bin') resources[buffer.uri] = fs.readFileSync(path.resolve(path.dirname(source), buffer.uri));
// Textures are deliberately not read or modified. Authored pigments are retained as vertex colours.
for (const image of json.images ?? []) if (image.uri) resources[image.uri] = new Uint8Array(4);
const input = await io.readJSON({ json, resources });
const doc = new Document(), buffer = doc.createBuffer(), scene = doc.createScene('distant-scenery');
const material = doc.createMaterial('scenery').setBaseColorFactor([1, 1, 1, 1]).setRoughnessFactor(1).setMetallicFactor(0);
const groups = new Map();
let sourceTriangles = 0;
const bucket = name => {
  if (!groups.has(name)) groups.set(name, { positions: [], colours: [], indices: [] });
  return groups.get(name);
};
const world = (m, p) => [0, 1, 2].map(k => m[k] * p[0] + m[k + 4] * p[1] + m[k + 8] * p[2] + m[k + 12]);
const groundColours = { asphalt: [.16, .17, .18], grass: [.22, .29, .12], gravel: [.36, .32, .25], water: [.10, .19, .24], concrete: [.43, .43, .40], stone: [.35, .34, .32] };
for (const node of input.getRoot().listNodes()) {
  const mesh = node.getMesh(); if (!mesh) continue;
  const extras = node.getExtras(), layer = extras.layer;
  const vegetation = layer === 'vegetation', ground = layer === 'ground' || layer === 'basic';
  if (!['ground', 'basic', 'buildings', 'buildings_outer', 'landmarks', 'bridges', 'vegetation'].includes(layer)) continue;
  if (vegetation && !String(extras.material_key).includes('foliage')) continue;
  const target = bucket(vegetation ? 'vegetation' : ground ? 'ground' : 'buildings');
  const matrix = node.getWorldMatrix(), instances = node.getExtension('EXT_mesh_gpu_instancing');
  for (const primitive of mesh.listPrimitives()) {
    const position = primitive.getAttribute('POSITION'); if (!position || primitive.getMode() !== 4) continue;
    sourceTriangles += (primitive.getIndices()?.getCount() ?? position.getCount()) / 3;
    const authored = primitive.getAttribute('_KIT_WALL') ?? primitive.getAttribute('COLOR_0');
    let colour = primitive.getMaterial()?.getBaseColorFactor().slice(0, 3) ?? [.6, .6, .6];
    if (ground) {
      const key = Object.keys(groundColours).find(key => String(extras.material_key ?? extras.surface).includes(key));
      if (key) colour = groundColours[key];
    }
    if (vegetation) {
      const translations = instances?.getAttribute('TRANSLATION'); if (!translations) continue;
      const scales = instances.getAttribute('SCALE');
      const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
      for (let i = 0; i < position.getCount(); i++) {
        const p = position.getElement(i, []); for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], p[k]); hi[k] = Math.max(hi[k], p[k]); }
      }
      // One eight-triangle crown retains each authored tree's footprint and height at skyline scale.
      for (let i = 0; i < translations.getCount(); i++) {
        const t = translations.getElement(i, []), s = scales?.getElement(i, []) ?? [1, 1, 1];
        const c = lo.map((v, k) => (v + hi[k]) / 2), r = lo.map((v, k) => (hi[k] - v) / 2);
        const base = target.positions.length / 3;
        for (const p of [[0, 1, 0], [1, 0, 0], [0, 0, 1], [-1, 0, 0], [0, 0, -1], [0, -1, 0]]) {
          target.positions.push(...world(matrix, p.map((v, k) => (c[k] + v * r[k]) * s[k] + t[k])));
          target.colours.push(...colour);
        }
        for (const j of [0, 2, 1, 0, 3, 2, 0, 4, 3, 0, 1, 4, 5, 1, 2, 5, 2, 3, 5, 3, 4, 5, 4, 1]) target.indices.push(base + j);
      }
      continue;
    }
    // Weld discarded normal/UV seams before simplifying; retain authored colour discontinuities.
    const positions = [], colours = [], remap = [], welded = new Map();
    let high = -Infinity;
    for (let i = 0; i < position.getCount(); i++) {
      const p = world(matrix, position.getElement(i, []));
      const c = authored?.getElement(i, []).slice(0, 3) ?? colour;
      const key = [...p.map(v => Math.round(v * 1000)), ...c.map(v => Math.round(v * 255))].join(',');
      let index = welded.get(key);
      if (index === undefined) { index = positions.length / 3; welded.set(key, index); positions.push(...p); colours.push(...c); }
      remap.push(index); high = Math.max(high, p[1]);
    }
    const indices = Uint32Array.from(primitive.getIndices()?.getArray() ?? remap.map((_, i) => i), i => remap[i]);
    const vertices = new Float32Array(positions), coloursArray = new Float32Array(colours);
    const locks = Uint8Array.from({ length: positions.length / 3 }, (_, i) =>
      ground ? [positions[i * 3], positions[i * 3 + 2]].some(v => Math.abs(v / 250 - Math.round(v / 250)) < .00001) ? 1 : 0
        : positions[i * 3 + 1] >= high - .05 ? 1 : 0);
    const [reduced] = MeshoptSimplifier.simplifyWithAttributes(indices, vertices, 3, coloursArray, 3, [1, 1, 1], locks,
      Math.max(3, Math.floor(indices.length * (ground ? .08 : .3) / 3) * 3), ground ? .5 : .25, ['ErrorAbsolute', 'Permissive']);
    const compact = new Map();
    for (const index of reduced) {
      let mapped = compact.get(index);
      if (mapped === undefined) { mapped = target.positions.length / 3; compact.set(index, mapped);
        target.positions.push(...positions.slice(index * 3, index * 3 + 3)); target.colours.push(...colours.slice(index * 3, index * 3 + 3)); }
      target.indices.push(mapped);
    }
  }
}
let triangles = 0;
for (const [layer, data] of groups) {
  if (!data.indices.length) continue;
  triangles += data.indices.length / 3;
  const attr = (name, type, array) => doc.createAccessor(name).setBuffer(buffer).setType(type).setArray(array);
  const primitive = doc.createPrimitive().setAttribute('POSITION', attr('position', 'VEC3', new Float32Array(data.positions)))
    .setAttribute('COLOR_0', attr('colour', 'VEC3', new Float32Array(data.colours)))
    .setIndices(attr('index', 'SCALAR', new Uint32Array(data.indices))).setMaterial(material);
  scene.addChild(doc.createNode(layer).setExtras({ layer, scenery: true }).setMesh(doc.createMesh(layer).addPrimitive(primitive)));
}
await doc.transform(meshopt({ encoder: MeshoptEncoder, level: 'medium', quantizePosition: 16, quantizeColor: 8 }));
await io.write(output, doc);
console.log(JSON.stringify({ sourceTriangles, triangles, draws: groups.size, sourceBytes: raw.length,
  bytes: fs.statSync(output).size, seconds: (performance.now() - started) / 1000 }));
