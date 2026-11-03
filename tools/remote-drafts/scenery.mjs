// Read-only source conversion. Derived distant geometry never replaces a source tile or its textures.
import fs from 'node:fs';
import { nightKey, nightEmission } from './scenery-night.mjs';
import path from 'node:path';
import { require, io, readSource, world } from './scenery-source.mjs';
import { openingKind, openingAtlas, programFields, transformedUv, reduceFacade } from './scenery-openings.mjs';
const { Document } = require('@gltf-transform/core');
const { reorder, quantize } = require('@gltf-transform/functions');
const { EXTMeshoptCompression } = require('@gltf-transform/extensions');
const { MeshoptEncoder, MeshoptSimplifier } = await import(require.resolve('meshoptimizer'));
const sharp = require('sharp');
await Promise.all([MeshoptEncoder.ready, MeshoptSimplifier.ready]);
io.registerDependencies({ 'meshopt.encoder': MeshoptEncoder });
const [source, output] = process.argv.slice(2);
if (!source || !output || path.resolve(source) === path.resolve(output)) throw new Error('Distinct source and derivative output required');
const started = performance.now(), raw = fs.readFileSync(source);
const input = await readSource(source);
const doc = new Document(), buffer = doc.createBuffer(), scene = doc.createScene('distant-scenery');
const material = doc.createMaterial('scenery').setBaseColorFactor([1, 1, 1, 1]).setRoughnessFactor(1).setMetallicFactor(0);
const groups = new Map();
const mapped = input.getRoot().listNodes().filter(node => ['buildings','buildings_outer','landmarks'].includes(node.getExtras().layer))
  .flatMap(node => node.getMesh()?.listPrimitives().filter(prim => openingKind(prim,node.getExtras())==='mapped') ?? []);
const atlas = await openingAtlas(doc,mapped,sharp);
let sourceTriangles = 0;
const bucket = name => {
  if (!groups.has(name)) groups.set(name, { positions: [], colours: [], indices: [], fields: new Map() });
  return groups.get(name);
};
const groundColours = { asphalt: [.16, .17, .18], grass: [.22, .29, .12], gravel: [.36, .32, .25], water: [.10, .19, .24], concrete: [.43, .43, .40], stone: [.35, .34, .32] };
for (const node of input.getRoot().listNodes()) {
  const mesh = node.getMesh(); if (!mesh) continue;
  const extras = node.getExtras(), layer = extras.layer;
  const vegetation = layer === 'vegetation', ground = layer === 'ground' || layer === 'basic';
  if (!['ground', 'basic', 'buildings', 'buildings_outer', 'landmarks', 'bridges', 'boats', 'vegetation', 'structures'].includes(layer)) continue;
  // Generic structures stay near-only; their night emitters (quay LED bars) persist at distance.
  if (layer === 'structures' && !mesh.listPrimitives().some(p => nightKey(p, extras))) continue;
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
    const night=nightKey(primitive,extras);
    if(layer==='structures' && !night) continue;
    if(night && night!=='night_landmark_wall') {
      // Thin emitters and coarse slabs are already compact. Preserve their authored vertices
      // so simplification cannot erase a light band or smear a top/soffit boundary.
      const group=bucket('night-architecture'),base=group.positions.length/3;
      if(!group.fields.has('_NIGHT_IRRADIANCE'))group.fields.set('_NIGHT_IRRADIANCE',{size:4,values:[]});
      for(let i=0;i<position.getCount();i++) {
        group.positions.push(...world(matrix,position.getElement(i,[])));
        group.colours.push(...colour);
        group.fields.get('_NIGHT_IRRADIANCE').values.push(...nightEmission(primitive,night,i,matrix));
      }
      const indices=primitive.getIndices()?.getArray() ?? Array.from({length:position.getCount()},(_,i)=>i);
      for(const i of indices)group.indices.push(base+i);
      continue;
    }
    const kind = ground ? 'none' : openingKind(primitive, extras);
    if (kind !== 'none') {
      // Keep the wall's UV-to-world mapping and constant instructions intact.
      // Shell/terrain simplification must never move or re-phase an opening.
      const nightMapped=night==='night_landmark_wall' && primitive.getAttribute('_NIGHT_IRRADIANCE');
      const schema=kind==='program' && primitive.getAttribute('_KIT_LAYOUT').getElementSize()===4 ? '-v6' : '';
      const group = bucket(`buildings-${nightMapped?'night-mapped':kind}${schema}`), base=group.positions.length/3;
      const fields=kind==='program' ? ['TEXCOORD_0',...programFields] : ['TEXCOORD_0','_FAR_RECT','_FAR_REPEAT'];
      if(nightMapped)fields.push('_NIGHT_IRRADIANCE');
      const entry=atlas.byMaterial.get(primitive.getMaterial());
      const reduced=reduceFacade(primitive,matrix,MeshoptSimplifier);
      for(const i of reduced.vertices) {
        group.positions.push(...world(matrix,position.getElement(i,[])));
        group.colours.push(...(kind==='program'?[1,1,1]:colour));
        for(const key of fields){
          let values;
          if(key==='_FAR_RECT')values=entry.rect;
          else if(key==='_FAR_REPEAT')values=entry.repeat;
          else if(key==='_KIT_GLAZING'&&!primitive.getAttribute(key))values=[0,0,0,-1];
          else if(key==='_KIT_FRAME'&&!primitive.getAttribute(key))values=[...primitive.getAttribute('_KIT_TRIM').getElement(i,[]),0];
          else values=primitive.getAttribute(key).getElement(i,[]);
          if(key==='TEXCOORD_0'&&kind==='mapped') values=transformedUv(primitive.getMaterial().getBaseColorTextureInfo(),values);
          if(!group.fields.has(key))group.fields.set(key,{size:values.length,values:[]});
          group.fields.get(key).values.push(...values);
        }
      }
      for(const i of reduced.indices)group.indices.push(base+i);
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
let triangles = 0, draws = 0;
for (const [layer, data] of groups) {
  if (!data.indices.length) continue;
  triangles += data.indices.length / 3; draws++;
  const attr = (name, type, array) => doc.createAccessor(name).setBuffer(buffer).setType(type).setArray(array);
  const primitive = doc.createPrimitive().setAttribute('POSITION', attr('position', 'VEC3', new Float32Array(data.positions)))
    .setAttribute('COLOR_0', attr('colour', 'VEC3', new Float32Array(data.colours)))
    .setIndices(attr('index', 'SCALAR', new Uint32Array(data.indices))).setMaterial(['buildings-mapped','buildings-night-mapped'].includes(layer)?atlas.material:material);
  for(const [key,field] of data.fields)primitive.setAttribute(key,attr(key,`VEC${field.size}`,new Float32Array(field.values)));
  scene.addChild(doc.createNode(layer).setExtras({ layer, scenery: true }).setMesh(doc.createMesh(layer).addPrimitive(primitive)));
}
await doc.transform(reorder({encoder:MeshoptEncoder,target:'size'}),
  quantize({quantizePosition:16,quantizeColor:8,pattern:/^(POSITION|COLOR_0)$/}));
doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({method:EXTMeshoptCompression.EncoderMethod.QUANTIZE});
await io.write(output, doc);
console.log(JSON.stringify({ sourceTriangles, triangles, draws, sourceBytes: raw.length,
  atlasBytes:atlas.bytes??0, mappedMaterials:atlas.entries??0, bytes: fs.statSync(output).size, seconds: (performance.now() - started) / 1000 }));
