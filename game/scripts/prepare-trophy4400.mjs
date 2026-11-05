// Prepares the #19-style 4400 trophy truck (SPEC section 2.6): raw Blender export -> runtime GLB + hardpoints.json.
// Validates the node contract, makes every material single-sided except the window nets, encodes the textures as
// KTX2 (ETC1S; base colour sRGB, metallic-roughness linear), meshopt-compresses the geometry (positions 14 bit) and
// writes the static-pose hardpoints the linkage solver (src/actors/trophy4400/linkage.ts) and its tests read.
// Usage: node scripts/prepare-trophy4400.mjs [raw.glb] [out dir]
//   raw.glb defaults to ~/turku3d/data/assets-src/trophy4400/trophy4400_raw.glb (local, not in git).
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS, EXTMeshoptCompression, KHRTextureBasisu } from '@gltf-transform/extensions';
import { prune, quantize, reorder } from '@gltf-transform/functions';
import { MeshoptDecoder, MeshoptEncoder } from 'meshoptimizer';
import { encodeToKTX2 } from 'ktx2-encoder';
import sharp from 'sharp';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const raw = process.argv[2] ?? path.join(os.homedir(), 'turku3d/data/assets-src/trophy4400/trophy4400_raw.glb');
const out = process.argv[3] ?? new URL('../public/assets/vehicles/trophy4400/', import.meta.url).pathname;
const cacheDir = path.join(path.dirname(raw), 'build/ktx2');
fs.mkdirSync(cacheDir, { recursive: true });

const io = new NodeIO().registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder });
await MeshoptEncoder.ready;
const doc = await io.read(raw);
const root = doc.getRoot();
const byName = new Map(root.listNodes().map(n => [n.getName(), n]));

// ---- node contract (SPEC 2.2/2.3) ----
const CORNERS = ['fl', 'fr', 'rl', 'rr'];
const required = ['trophy4400', 'body', 'steer-wheel', 'rack-front-slider', 'rack-rear-slider', 'spare', 'antenna',
  'lamp-front-left', 'lamp-front-right', 'lamp-bar', 'lamp-pod-left', 'lamp-pod-right', 'tail-left', 'tail-right', 'reverse',
  'seat-driver', 'seat-passenger', 'exit-left', 'hardpoints',
  'hp-rack-front-axis-l', 'hp-rack-front-axis-r', 'hp-rack-rear-axis-l', 'hp-rack-rear-axis-r'];
const HP = { lcaInF: 'lca-in-f', lcaInR: 'lca-in-r', lcaOut: 'lca-out', ucaInF: 'uca-in-f', ucaInR: 'uca-in-r', ucaOut: 'uca-out',
  hub: 'hub', tierodIn: 'tierod-in', tierodOut: 'tierod-out', coilTop: 'coil-top', coilBot: 'coil-bot',
  bypassTop: 'bypass-top', bypassBot: 'bypass-bot', cvIn: 'cv-in', cvOut: 'cv-out' };
for (const c of CORNERS) {
  required.push(`uca-${c}`, `lca-${c}`, `upright-${c}`, `wheel-${c}`, `tierod-${c}`, `coil-${c}-body`, `coil-${c}-shaft`,
    `coil-${c}-spring`, `bypass-${c}-body`, `bypass-${c}-shaft`, `cv-${c}-inner`, `cv-${c}-shaft`, `cv-${c}-outer`);
  for (const hp of Object.values(HP)) required.push(`hp-${c}-${hp}`);
}
const missing = required.filter(n => !byName.has(n));
if (missing.length) throw new Error(`node contract: missing ${missing.join(', ')}`);

// ---- hardpoints (static pose, GLB ground frame) ----
const at = name => byName.get(name).getWorldTranslation().map(v => +v.toFixed(4));
const hardpoints = {
  frame: 'glb-ground', units: 'm',
  corners: Object.fromEntries(CORNERS.map(c => [c, Object.fromEntries(Object.entries(HP).map(([k, n]) => [k, at(`hp-${c}-${n}`)]))])),
  racks: { front: { l: at('hp-rack-front-axis-l'), r: at('hp-rack-front-axis-r') }, rear: { l: at('hp-rack-rear-axis-l'), r: at('hp-rack-rear-axis-r') } },
  travel: { front: { bump: 0.36, droop: 0.30 }, rear: { bump: 0.38, droop: 0.33 } },
  // Spring rest: the top seat (node origin) and the modelled coil length along its local +Y (from the L0 mesh).
  springs: Object.fromEntries(CORNERS.map(c => {
    const mesh = byName.get(`coil-${c}-spring_L0`)?.getMesh();
    if (!mesh) throw new Error(`node contract: missing coil-${c}-spring_L0`);
    let lo = Infinity, hi = -Infinity;
    for (const p of mesh.listPrimitives()) { const a = p.getAttribute('POSITION'); lo = Math.min(lo, a.getMin([])[1]); hi = Math.max(hi, a.getMax([])[1]); }
    return [c, { seat: at(`coil-${c}-spring`), length: +(hi - lo).toFixed(4) }];
  })),
};

// ---- materials ----
for (const m of root.listMaterials()) m.setDoubleSided(m.getName() === 'net-black');

// ---- textures -> KTX2 ----
const srgbOf = new Map();
for (const m of root.listMaterials()) {
  if (m.getBaseColorTexture()) srgbOf.set(m.getBaseColorTexture(), true);
  if (m.getEmissiveTexture()) srgbOf.set(m.getEmissiveTexture(), true);
  for (const t of [m.getMetallicRoughnessTexture(), m.getNormalTexture(), m.getOcclusionTexture()]) if (t) srgbOf.set(t, false);
}
doc.createExtension(KHRTextureBasisu).setRequired(true);
let gpuBytes = 0;
for (const texture of root.listTextures()) {
  const srgb = srgbOf.get(texture) ?? true;
  const { data, info } = await sharp(Buffer.from(texture.getImage())).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  const key = createHash('sha256').update(data).update(JSON.stringify({ srgb, w: info.width, h: info.height, v: 1 })).digest('hex');
  const file = path.join(cacheDir, `${key}.ktx2`);
  if (!fs.existsSync(file)) {
    const ktx = await encodeToKTX2(new Uint8Array(1), {
      isUASTC: false, generateMipmap: true, isPerceptual: srgb, isSetKTX2SRGBTransferFunc: srgb, qualityLevel: 200, compressionLevel: 2,
      imageDecoder: async () => ({ width: info.width, height: info.height, data: new Uint8Array(data.buffer, data.byteOffset, data.length) }),
    });
    fs.writeFileSync(file, ktx);
  }
  texture.setImage(new Uint8Array(fs.readFileSync(file))).setMimeType('image/ktx2');
  // ETC1S transcodes to BC7/ASTC at 1 byte per texel (BC1/ETC at 0.5); count the worse case with mips.
  gpuBytes += info.width * info.height * 4 / 3;
}

// ---- geometry: meshopt ----
await doc.transform(prune({ keepLeaves: true, keepAttributes: true }), reorder({ encoder: MeshoptEncoder }),
  quantize({ quantizePosition: 14, quantizeNormal: 10, quantizeTexcoord: 12, quantizationVolume: 'mesh' }));
doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.QUANTIZE });

// ---- budget table (SPEC 2.5) ----
const lods = [0, 1, 2].map(() => ({ triangles: 0, draws: 0 }));
for (const node of root.listNodes()) {
  const lod = /_L(\d)$/.exec(node.getName());
  if (!lod || !node.getMesh()) continue;
  for (const p of node.getMesh().listPrimitives()) {
    const count = p.getIndices()?.getCount() ?? p.getAttribute('POSITION').getCount();
    lods[+lod[1]].triangles += count / 3; lods[+lod[1]].draws++;
  }
}
// The L2 level shows wheels' and body's L2 meshes only; linkage nodes without an _L2 child are hidden there.
fs.mkdirSync(out, { recursive: true });
const glb = path.join(out, 'trophy4400.glb');
await io.write(glb, doc);
fs.writeFileSync(path.join(out, 'hardpoints.json'), JSON.stringify(hardpoints, null, 1) + '\n');
const bytes = fs.readFileSync(glb);
console.log('| LOD | triangles | draws |\n|---|---|---|');
lods.forEach((l, i) => console.log(`| L${i} | ${l.triangles.toLocaleString('en')} | ${l.draws} |`));
console.log(`GLB ${(bytes.length / 1e6).toFixed(2)} MB, textures ≤ ${(gpuBytes / 1e6).toFixed(1)} MB on the GPU, sha256 ${createHash('sha256').update(bytes).digest('hex')}`);
