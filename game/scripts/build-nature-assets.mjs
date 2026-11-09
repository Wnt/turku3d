#!/usr/bin/env node
// Builds Turku3D's environment asset library: public/assets/nature/nature.glb (trees, shrubs, hedge, grass, ground
// cover and the fence/railing/wall kit with their LOD chains and impostors) and nature.json (per-asset numbers).
// Sources and licences: public/assets/nature/README.md.
//
//   node scripts/build-nature-assets.mjs [--src ~/turku3d/data/assets-src/nature] [--out public/assets/nature] [--previews DIR]
//
// Raw sources stay outside git under --src; encoded KTX2 textures are cached there by content hash.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { Document, NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS, EXTMeshoptCompression, KHRTextureBasisu } from '@gltf-transform/extensions';
import { quantize, reorder } from '@gltf-transform/functions';
import { MeshoptEncoder, MeshoptDecoder } from 'meshoptimizer';
import { Img, savePng, half } from './nature/image.mjs';
import { foliageAtlas, barkAtlas, propTiles } from './nature/textures.mjs';
import { SPECIES, TREES, SHRUBS, skeleton, meshTree, hedge, clump, MeshBuilder } from './nature/trees.mjs';
import { atlasSize, bakeTree, fitTree, impostorQuad, shippedNormal } from './nature/octahedral.mjs';
import { ktx2 as encodeKtx2 } from './nature/ktx2.mjs';

const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const here = path.dirname(new URL(import.meta.url).pathname);
const src = path.resolve(opt('--src', path.join(os.homedir(), 'turku3d/data/assets-src/nature')));
const out = path.resolve(opt('--out', path.join(here, '../public/assets/nature')));
const previews = opt('--previews', path.join(src, 'build/previews'));
const cacheDir = path.join(src, 'build/ktx2');
fs.mkdirSync(out, { recursive: true }); fs.mkdirSync(cacheDir, { recursive: true }); fs.mkdirSync(previews, { recursive: true });
const log = (...m) => console.log(`[nature ${((performance.now()) / 1000).toFixed(1)}s]`, ...m);

// ------------------------------------------------------------------ textures
log('composing foliage, bark and prop textures');
const foliage = await foliageAtlas(src);
const bark = await barkAtlas(src);
const props = await propTiles(src);

// ------------------------------------------------------------------ vegetation geometry + impostors
// 16 tree models (8 species x 2 variants), each as 8 x 8 hemi-octahedral views (scripts/nature/octahedral.mjs).
const assets = [];

function radialExtent(meshes) {
  let R = 0, top = 0;
  for (const m of meshes) for (let i = 0; i < m.positions.length; i += 3) {
    R = Math.max(R, Math.hypot(m.positions[i], m.positions[i + 2])); top = Math.max(top, m.positions[i + 1]);
  }
  return { R, top };
}

const SEED = { birch: 11, linden: 23, maple: 31, oak: 47, rowan: 53, willow: 61, pine: 71, spruce: 83, lilac: 97, rose: 101, juniper: 107 };
/** Two seeded variants per species (`tree_birch`, `tree_birch_v2`): neighbouring trees of one species differ. */
const VARIANTS = 2, variantName = (base, v) => v ? `${base}_v${v + 1}` : base;
const impostorAtlas = atlasSize(TREES.length * VARIANTS);
const impostorAlbedo = new Img(impostorAtlas.width, impostorAtlas.height), impostorNormal = new Img(impostorAtlas.width, impostorAtlas.height);
const copyMesh = mesh => Object.assign(new MeshBuilder(), mesh, { positions: [...mesh.positions] });
for (const [i, name] of TREES.entries()) for (let v = 0; v < VARIANTS; v++) {
  // Mature spruce exposes its drooping scaffold; smaller cards avoid solid triangular shelves.
  const sp = name === 'spruce' ? { ...SPECIES[name], crownBase: v ? .20 : .15, card: .85, flatCards: .10 } : SPECIES[name];
  const seed = SEED[name] + 1000 * v, sk = skeleton(name, seed, sp), index = i * VARIANTS + v;
  const lod0 = meshTree(sk, sp, 0, seed), lod1 = meshTree(sk, sp, 1, seed);
  const { R, top } = radialExtent([lod0.bark, lod0.leaves]);
  // The impostor's views show the crown the runtime draws: the full LOD after its lidar crown fit (nature/crowns.ts),
  // with the manifest's rounded height and radius as the runtime reads them.
  const fitted = [{ mesh: copyMesh(lod0.bark), texture: bark.albedo, cutout: false }, { mesh: copyMesh(lod0.leaves), texture: foliage.atlas, cutout: true }];
  const extent = await fitTree(fitted, name, +top.toFixed(2), +R.toFixed(2));
  bakeTree(impostorAlbedo, impostorNormal, index, fitted, extent, top);
  const lod2 = impostorQuad(MeshBuilder, index, extent, top);
  assets.push({ name: variantName(`tree_${name}`, v), kind: 'tree', species: name, height: top, radius: R, crownBase: sk.crown.y0,
    lods: [{ bark: lod0.bark, leaves: lod0.leaves }, { bark: lod1.bark, leaves: lod1.leaves }, { impostor: lod2 }] });
  log(`tree ${variantName(name, v)}: lod0 ${lod0.bark.triangles + lod0.leaves.triangles} tris, lod1 ${lod1.bark.triangles + lod1.leaves.triangles}, H ${top.toFixed(1)} R ${R.toFixed(1)}`);
}
for (const name of SHRUBS) for (let v = 0; v < VARIANTS; v++) {
  const sp = SPECIES[name], seed = SEED[name] + 1000 * v, sk = skeleton(name, seed);
  const lod0 = meshTree(sk, sp, 0, seed), lod1 = meshTree(sk, sp, 1, seed);
  const { R, top } = radialExtent([lod0.bark, lod0.leaves]);
  assets.push({ name: variantName(`shrub_${name}`, v), kind: 'shrub', species: name, height: top, radius: R, lods: [{ bark: lod0.bark, leaves: lod0.leaves }, { bark: lod1.bark, leaves: lod1.leaves }] });
  log(`shrub ${variantName(name, v)}: lod0 ${lod0.bark.triangles + lod0.leaves.triangles} tris, lod1 ${lod1.bark.triangles + lod1.leaves.triangles}`);
}
{
  const h0 = hedge(131, 0), h1 = hedge(131, 1);
  assets.push({ name: 'hedge', kind: 'hedge', species: 'privet', height: h0.height, radius: 1, module: 2, lods: [{ leaves: h0.leaves }, { leaves: h1.leaves }] });
}
for (const kind of ['grass', 'lawn', 'meadow', 'rosette']) {
  const c = clump(kind, 140 + kind.length);
  const footprint = radialExtent([c.leaves]);
  assets.push({ name: kind === 'rosette' ? 'groundcover_rosette' : `grass_${kind === 'grass' ? 'clump' : kind === 'lawn' ? 'lawn' : 'meadow'}`, kind: 'grass', height: footprint.top, radius: Math.ceil(footprint.R * 100) / 100, lods: [{ leaves: c.leaves }] });
}

// ------------------------------------------------------------------ fence kit (modelled in Blender through MCP)
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder });
{
  const kit = await io.read(path.join(src, 'blender/fence-kit.glb'));
  const byName = new Map();
  for (const node of kit.getRoot().listNodes()) {
    const mesh = node.getMesh();
    if (!mesh) continue;
    const m = node.getWorldMatrix(), builder = { wood: null, metal: null, granite: null, chain: null };
    for (const prim of mesh.listPrimitives()) {
      const material = { paint_wood: 'wood', painted_metal: 'metal', galvanised: 'metal', granite: 'granite', chain_mesh: 'chain' }[prim.getMaterial()?.getName()];
      if (!material) throw new Error(`fence kit: unknown material ${prim.getMaterial()?.getName()}`);
      const b = builder[material] ??= new MeshBuilder();
      const P = prim.getAttribute('POSITION'), N = prim.getAttribute('NORMAL'), T = prim.getAttribute('TEXCOORD_0'), I = prim.getIndices();
      const base = b.vertexCount, p = [], n = [], t = [];
      for (let i = 0; i < P.getCount(); i++) {
        P.getElement(i, p); N.getElement(i, n); if (T) T.getElement(i, t); else t.splice(0, 2, 0, 0);
        const x = m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12], y = m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13], z = m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14];
        const nx = m[0] * n[0] + m[4] * n[1] + m[8] * n[2], ny = m[1] * n[0] + m[5] * n[1] + m[9] * n[2], nz = m[2] * n[0] + m[6] * n[1] + m[10] * n[2];
        const l = Math.hypot(nx, ny, nz) || 1;
        b.vertex([x, y, z], [nx / l, ny / l, nz / l], [t[0], t[1]], [1, 1, 1], [0, 0, 0, 0]);
      }
      for (let i = 0; i < I.getCount(); i++) b.indices.push(base + I.getScalar(i));
    }
    byName.set(node.getName(), builder);
  }
  for (const name of ['picket_fence', 'board_fence', 'metal_railing', 'park_fence', 'chain_fence', 'granite_wall']) {
    const lods = [0, 1].map(k => {
      const b = byName.get(`${name}_lod${k}`);
      if (!b) throw new Error(`fence kit: missing ${name}_lod${k}`);
      return b;
    });
    assets.push({ name, kind: 'fence', module: 2, height: Math.max(...lods[0][Object.keys(lods[0]).find(k => lods[0][k])].bounds().max.slice(1, 2)), radius: 1, lods });
  }
}

// ------------------------------------------------------------------ previews (local only, not shipped)
await savePng(foliage.atlas, path.join(previews, 'foliage.png'));
await savePng(impostorAlbedo, path.join(previews, 'impostor-albedo.png'));
await savePng(impostorNormal, path.join(previews, 'impostor-normal-depth.png'));
await savePng(bark.albedo, path.join(previews, 'bark.png'), { alpha: false });
await savePng(bark.normal, path.join(previews, 'bark-normal.png'), { alpha: false });
for (const [name, img] of Object.entries(props)) await savePng(img, path.join(previews, `${name}.png`), { alpha: name === 'chain' });

// ------------------------------------------------------------------ KTX2
const ktx2 = (img, opts) => encodeKtx2(img, opts, cacheDir, log);

// ------------------------------------------------------------------ glTF
log('writing glTF');
const doc = new Document();
doc.createExtension(KHRTextureBasisu).setRequired(true);
const buffer = doc.createBuffer();
const textures = {};
const addTexture = async (name, img, opts) => {
  const data = await ktx2(img, opts);
  textures[name] = { width: img.w, height: img.h, bytes: data.length, format: opts.normal ? 'UASTC+zstd' : 'ETC1S' };
  return doc.createTexture(name).setMimeType('image/ktx2').setImage(new Uint8Array(data));
};
const tex = {
  foliage: await addTexture('nature_foliage', foliage.atlas, { srgb: true, quality: 230 }),
  bark: await addTexture('nature_bark', bark.albedo, { srgb: true, quality: 200 }),
  barkNormal: await addTexture('nature_bark_normal', half(bark.normal),{ srgb: false, normal: true }),
  impostor: await addTexture('nature_impostor', impostorAlbedo, { srgb: true, quality: 230 }),
  impostorNormal: await addTexture('nature_impostor_normal', shippedNormal(impostorNormal), { srgb: false, normal: true }),
  wood: await addTexture('nature_wood', props.wood, { srgb: true }),
  woodNormal: await addTexture('nature_wood_normal', props.woodNormal, { srgb: false, normal: true }),
  granite: await addTexture('nature_granite', props.granite, { srgb: true }),
  graniteNormal: await addTexture('nature_granite_normal', props.graniteNormal, { srgb: false, normal: true }),
  chain: await addTexture('nature_chain', props.chain, { srgb: true, quality: 255 }),
};
const materials = {
  bark: doc.createMaterial('nature_bark').setBaseColorTexture(tex.bark).setNormalTexture(tex.barkNormal).setRoughnessFactor(0.92).setMetallicFactor(0),
  leaves: doc.createMaterial('nature_leaves').setBaseColorTexture(tex.foliage).setAlphaMode('MASK').setAlphaCutoff(0.5).setDoubleSided(true).setRoughnessFactor(0.75).setMetallicFactor(0),
  impostor: doc.createMaterial('nature_impostor').setBaseColorTexture(tex.impostor).setNormalTexture(tex.impostorNormal).setAlphaMode('MASK').setAlphaCutoff(0.5).setDoubleSided(true).setRoughnessFactor(0.8).setMetallicFactor(0),
  wood: doc.createMaterial('nature_wood').setBaseColorTexture(tex.wood).setNormalTexture(tex.woodNormal).setRoughnessFactor(0.8).setMetallicFactor(0),
  // Steel colour comes from the instance tint (black or green paint, galvanised grey).
  metal: doc.createMaterial('nature_metal').setBaseColorFactor([1, 1, 1, 1]).setRoughnessFactor(0.45).setMetallicFactor(0.6),
  chain: doc.createMaterial('nature_chain').setBaseColorTexture(tex.chain).setAlphaMode('MASK').setAlphaCutoff(0.5).setDoubleSided(true).setRoughnessFactor(0.5).setMetallicFactor(0.7),
  granite: doc.createMaterial('nature_granite').setBaseColorTexture(tex.granite).setNormalTexture(tex.graniteNormal).setRoughnessFactor(0.85).setMetallicFactor(0),
};
const scene = doc.createScene('nature');
const accessor = (array, type, normalized = false) => doc.createAccessor().setArray(array).setType(type).setNormalized(normalized).setBuffer(buffer);
const u8 = values => Uint8Array.from(values, v => Math.max(0, Math.min(255, Math.round(v * 255))));
function primitive(mesh, material) {
  const prim = doc.createPrimitive().setMaterial(material)
    .setAttribute('POSITION', accessor(new Float32Array(mesh.positions), 'VEC3'))
    .setAttribute('TEXCOORD_0', accessor(new Float32Array(mesh.uvs), 'VEC2'));
  if (mesh.impostor) {
    // The impostor quad: its tree's bounding cylinder and atlas slot. The views carry the shading and the normals, and
    // it does not sway: no vertex normal, colour or wind (a vertex stage takes 16 attributes, nature/impostor.ts).
    prim.setAttribute('_IMPOSTOR', accessor(new Float32Array(mesh.impostor), 'VEC4'));
  } else {
    prim.setAttribute('NORMAL', accessor(new Float32Array(mesh.normals), 'VEC3'));
    const colors = [];
    for (let i = 0; i < mesh.colors.length; i += 3) colors.push(mesh.colors[i], mesh.colors[i + 1], mesh.colors[i + 2], 1);
    prim.setAttribute('COLOR_0', accessor(u8(colors), 'VEC4', true));
    prim.setAttribute('_WIND', accessor(u8(mesh.wind), 'VEC4', true));
  }
  prim.setIndices(accessor(mesh.vertexCount > 65535 ? new Uint32Array(mesh.indices) : new Uint16Array(mesh.indices), 'SCALAR'));
  return prim;
}
const manifest = { version: 1, generated: 'scripts/build-nature-assets.mjs', units: 'metres, +Y up, base at origin', assets: {}, textures };
for (const asset of assets) {
  const entry = { kind: asset.kind, species: asset.species, height: +asset.height.toFixed(2), radius: +asset.radius.toFixed(2), module: asset.module, lods: [] };
  for (const [k, parts] of asset.lods.entries()) {
    const mesh = doc.createMesh(`${asset.name}.lod${k}`);
    let tris = 0, verts = 0;
    const draws = [];
    for (const [part, mb] of Object.entries(parts)) {
      if (!mb || !mb.triangles) continue;
      const material = part === 'bark' ? materials.bark : part === 'leaves' ? materials.leaves : part === 'impostor' ? materials.impostor
        : part === 'wood' ? materials.wood : part === 'metal' ? materials.metal : part === 'chain' ? materials.chain : materials.granite;
      mesh.addPrimitive(primitive(mb, material));
      tris += mb.triangles; verts += mb.vertexCount; draws.push(material.getName());
    }
    const node = doc.createNode(`${asset.name}.lod${k}`).setMesh(mesh).setExtras({ asset: asset.name, lod: k, kind: asset.kind });
    scene.addChild(node);
    entry.lods.push({ triangles: tris, vertices: verts, draws });
  }
  manifest.assets[asset.name] = entry;
}
scene.setExtras({ nature: { version: 1 } });
await MeshoptEncoder.ready;
// Positions (14 bit) and normals (10 bit) quantized per mesh; the runtime bakes the dequantizing node transform.
await doc.transform(reorder({ encoder: MeshoptEncoder }), quantize({ pattern: /^(POSITION|NORMAL)$/, quantizePosition: 14, quantizeNormal: 10, quantizationVolume: 'mesh' }));
doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.QUANTIZE });
const glbPath = path.join(out, 'nature.glb');
await io.write(glbPath, doc);
const glb = fs.readFileSync(glbPath);
manifest.glb = { bytes: glb.length, sha256: createHash('sha256').update(glb).digest('hex') };
fs.writeFileSync(path.join(out, 'nature.json'), JSON.stringify(manifest, null, 1) + '\n');
// The README's asset table mirrors nature.json.
const readme = path.join(out, 'README.md');
if (fs.existsSync(readme)) {
  const rows = Object.entries(manifest.assets).map(([name, a]) => `| \`${name}\` | ${a.species ?? a.kind} | ${a.height} | ` +
    [0, 1, 2].map(k => a.lods[k] ? `${a.lods[k].triangles.toLocaleString('en')}` : '-').join(' | ') + ' |');
  const text = fs.readFileSync(readme, 'utf8').replace(/<!-- table:begin -->[\s\S]*<!-- table:end -->/, `<!-- table:begin -->\n${rows.join('\n')}\n<!-- table:end -->`);
  fs.writeFileSync(readme, text);
}
log(`nature.glb ${(glb.length / 1024 / 1024).toFixed(2)} MiB, ${assets.length} assets`);
execFileSync('python3', [path.join(here, 'nature/calibrate-alpha.py'), out], { stdio: 'inherit' });

