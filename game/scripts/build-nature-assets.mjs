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
import { Document, NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS, EXTMeshoptCompression, KHRTextureBasisu } from '@gltf-transform/extensions';
import { quantize, reorder } from '@gltf-transform/functions';
import { MeshoptEncoder, MeshoptDecoder } from 'meshoptimizer';
import { encodeToKTX2 } from 'ktx2-encoder';
import { Img, toRGBA8, savePng, blit, dilate, half } from './nature/image.mjs';
import { foliageAtlas, barkAtlas, propTiles } from './nature/textures.mjs';
import { SPECIES, TREES, SHRUBS, skeleton, meshTree, hedge, clump, MeshBuilder } from './nature/trees.mjs';
import { bake } from './nature/raster.mjs';

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
// 16 tree models (8 species x 2 variants) x (3 side views + top view): 128 x 256 cells, a 2048 x 1024 atlas.
const IMP_W = 128, IMP_H = 256, IMP_COLS = 16, IMP_ROWS = 4;
const impostorAlbedo = new Img(IMP_W * IMP_COLS, IMP_H * IMP_ROWS), impostorNormal = new Img(IMP_W * IMP_COLS, IMP_H * IMP_ROWS);
for (let i = 0; i < impostorNormal.w * impostorNormal.h; i++) impostorNormal.data.set([0.5, 0.5, 1, 1], i * 4);
const assets = [];

function radialExtent(meshes) {
  let R = 0, top = 0;
  for (const m of meshes) for (let i = 0; i < m.positions.length; i += 3) {
    R = Math.max(R, Math.hypot(m.positions[i], m.positions[i + 2])); top = Math.max(top, m.positions[i + 1]);
  }
  return { R, top };
}

function impostorLod(index, R, top, yc) {
  const mesh = new MeshBuilder(), W = IMP_COLS * IMP_W, Hh = IMP_ROWS * IMP_H;
  const u0 = index * IMP_W / W + 0.5 / W, u1 = (index + 1) * IMP_W / W - 0.5 / W;
  // No tangents: the runtime derives the normal-map frame from UV derivatives, which follows any instance rotation.
  const quad = (corners, n) => {
    const ids = corners.map(([p, u, v]) => mesh.vertex(p, n, [u, v], [1, 1, 1], [Math.min(1, 0.4 * (p[1] / top) ** 2), 0, (0.37 * index) % 1, 0]));
    mesh.tri(ids[0], ids[1], ids[2]); mesh.tri(ids[1], ids[3], ids[2]);
  };
  for (let k = 0; k < 3; k++) {
    const a = k * Math.PI / 3, right = [Math.cos(a), 0, -Math.sin(a)], toward = [Math.sin(a), 0, Math.cos(a)];
    const w = R * 1.04, y0 = -0.01 * top, y1 = 1.01 * top, v0 = k * IMP_H / Hh + 0.5 / Hh, v1 = (k + 1) * IMP_H / Hh - 0.5 / Hh;
    quad([[[-right[0] * w, y0, -right[2] * w], u0, v1], [[right[0] * w, y0, right[2] * w], u1, v1],
      [[-right[0] * w, y1, -right[2] * w], u0, v0], [[right[0] * w, y1, right[2] * w], u1, v0]], toward);
  }
  const w = R * 1.04, v0 = 3 * IMP_H / Hh + 0.5 / Hh, v1 = (3 * IMP_H + IMP_W) / Hh - 0.5 / Hh;
  quad([[[-w, yc, w], u0, v1], [[w, yc, w], u1, v1], [[-w, yc, -w], u0, v0], [[w, yc, -w], u1, v0]], [0, 1, 0]);
  return mesh;
}

function bakeImpostor(index, parts, R, top) {
  for (let k = 0; k < 3; k++) {
    const a = k * Math.PI / 3;
    const view = { right: [Math.cos(a), 0, -Math.sin(a)], up: [0, 1, 0], toward: [Math.sin(a), 0, Math.cos(a)], centre: [0, top / 2, 0], width: 2 * R * 1.04, height: top * 1.02 };
    const { albedo, normal } = bake(parts, view, IMP_W, IMP_H);
    dilate(albedo, 16);
    blit(impostorAlbedo, albedo, index * IMP_W, k * IMP_H); blit(impostorNormal, normal, index * IMP_W, k * IMP_H);
  }
  const view = { right: [1, 0, 0], up: [0, 0, -1], toward: [0, 1, 0], centre: [0, top / 2, 0], width: 2 * R * 1.04, height: 2 * R * 1.04 };
  const { albedo, normal } = bake(parts, view, IMP_W, IMP_W);
  dilate(albedo, 16);
  blit(impostorAlbedo, albedo, index * IMP_W, 3 * IMP_H); blit(impostorNormal, normal, index * IMP_W, 3 * IMP_H);
}

const SEED = { birch: 11, linden: 23, maple: 31, oak: 47, rowan: 53, willow: 61, pine: 71, spruce: 83, lilac: 97, rose: 101, juniper: 107 };
/** Two seeded variants per species (`tree_birch`, `tree_birch_v2`): neighbouring trees of one species differ. */
const VARIANTS = 2, variantName = (base, v) => v ? `${base}_v${v + 1}` : base;
for (const [i, name] of TREES.entries()) for (let v = 0; v < VARIANTS; v++) {
  // Mature spruce exposes its drooping scaffold; smaller cards avoid solid triangular shelves.
  const sp = name === 'spruce' ? { ...SPECIES[name], crownBase: v ? .20 : .15, card: .85, flatCards: .10 } : SPECIES[name];
  const seed = SEED[name] + 1000 * v, sk = skeleton(name, seed, sp), index = i * VARIANTS + v;
  const lod0 = meshTree(sk, sp, 0, seed), lod1 = meshTree(sk, sp, 1, seed);
  const { R, top } = radialExtent([lod0.bark, lod0.leaves]);
  bakeImpostor(index, [{ mesh: lod0.bark, texture: bark.albedo, cutout: false }, { mesh: lod0.leaves, texture: foliage.atlas, cutout: true }], R, top);
  const lod2 = impostorLod(index, R, top, sk.crown.yc);
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
await savePng(impostorNormal, path.join(previews, 'impostor-normal.png'), { alpha: false });
await savePng(bark.albedo, path.join(previews, 'bark.png'), { alpha: false });
await savePng(bark.normal, path.join(previews, 'bark-normal.png'), { alpha: false });
for (const [name, img] of Object.entries(props)) await savePng(img, path.join(previews, `${name}.png`), { alpha: name === 'chain' });

// ------------------------------------------------------------------ KTX2
async function ktx2(img, { srgb, normal = false, quality = 200 }) {
  const raw = toRGBA8(img);
  const key = createHash('sha256').update(raw).update(JSON.stringify({ srgb, normal, quality, w: img.w, h: img.h, v: 2 })).digest('hex');
  const file = path.join(cacheDir, `${key}.ktx2`);
  if (fs.existsSync(file)) return fs.readFileSync(file);
  const started = performance.now();
  const data = await encodeToKTX2(new Uint8Array(1), {
    isUASTC: normal, generateMipmap: true, isPerceptual: srgb, isSetKTX2SRGBTransferFunc: srgb, isNormalMap: normal,
    needSupercompression: normal, uastcLDRQualityLevel: 2, enableRDO: normal, rdoQualityLevel: 2, qualityLevel: quality, compressionLevel: 2,
    imageDecoder: async () => ({ width: img.w, height: img.h, data: new Uint8Array(raw.buffer, raw.byteOffset, raw.length) }),
  });
  fs.writeFileSync(file, data);
  log(`encoded ${img.w}x${img.h} ${normal ? 'UASTC' : 'ETC1S'} ${(data.length / 1024).toFixed(0)} KiB in ${((performance.now() - started) / 1000).toFixed(1)} s`);
  return Buffer.from(data);
}

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
  impostorNormal: await addTexture('nature_impostor_normal', half(impostorNormal),{ srgb: false, normal: true }),
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
    .setAttribute('NORMAL', accessor(new Float32Array(mesh.normals), 'VEC3'))
    .setAttribute('TEXCOORD_0', accessor(new Float32Array(mesh.uvs), 'VEC2'));
  const colors = [];
  for (let i = 0; i < mesh.colors.length; i += 3) colors.push(mesh.colors[i], mesh.colors[i + 1], mesh.colors[i + 2], 1);
  prim.setAttribute('COLOR_0', accessor(u8(colors), 'VEC4', true));
  prim.setAttribute('_WIND', accessor(u8(mesh.wind), 'VEC4', true));
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

