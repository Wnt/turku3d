// Generates the dummy dev tileset in public/dev-tiles/ (docs/specs/tiles.md + col-format.md):
//   tileset.json        3D Tiles 1.0, flat: root (no content) + one L0 child t0_0_0
//   manifest.json       turku3d-manifest v1 with sha256/bytes for every file
//   visual/0/t0_0_0.glb glTF 2.0 + EXT_meshopt_compression + KHR_texture_basisu (ETC1S ground texture)
//   collision/t0_0_0/c_{i}_{j}.col   16 collision tiles, RAW encoding (.col v1.0)
// The scene is procedural: a flat ground at H = 2.0 m, a 12 cm curb/sidewalk north of the office spawn,
// a 6-step stair, a building box, a pole primitive, and two asymmetric markers (NE red, SW blue) that the
// e2e test uses to check the 3D Tiles Z-up <-> engine Y-up handling.
// Usage: node scripts/make-dev-tiles.mjs [outDir]   (default public/dev-tiles)
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import crypto from 'node:crypto';
import { Document, NodeIO } from '@gltf-transform/core';
import { EXTMeshoptCompression, KHRTextureBasisu } from '@gltf-transform/extensions';
import { MeshoptEncoder, MeshoptDecoder } from 'meshoptimizer';
import { encodeToKTX2 } from 'ktx2-encoder';

const OUT = path.resolve(process.argv[2] ?? 'public/dev-tiles');
const L0 = 250, COL = 62.5, G = 2.0; // tile sizes (m), ground height N2000 (m)
export const MAT = { road: 1, sidewalk: 2, curb: 3, paving_slab: 8, grass: 10, stair_slope: 16, wall: 19, building: 22, roof: 23, pole: 25 };
const COLOR = { 1: [0.32, 0.32, 0.34], 2: [0.62, 0.6, 0.57], 3: [0.72, 0.72, 0.7], 8: [0.6, 0.55, 0.5], 10: [0.3, 0.5, 0.25], 16: [0.65, 0.62, 0.6], 19: [0.6, 0.58, 0.55], 22: [0.78, 0.7, 0.58], 23: [0.45, 0.25, 0.2], 25: [0.2, 0.2, 0.22] };

// ---------------------------------------------------------------- geometry (engine coords, world-local)
/** parts: { mat, pos: number[], idx: number[], visualOnly?: bool, name? } */
const parts = [];
function quad(p, a, b, c, d) { // a,b,c,d counter-clockwise seen from the front side
  const o = p.pos.length / 3; p.pos.push(...a, ...b, ...c, ...d); p.idx.push(o, o + 1, o + 2, o, o + 2, o + 3);
}
/** axis-aligned box [x0,x1]x[y0,y1]x[z0,z1]; faces wound outward; bottom omitted when onGround */
function box(mat, x0, x1, y0, y1, z0, z1, opts = {}) {
  const p = { mat, pos: [], idx: [], ...opts };
  quad(p, [x0, y1, z1], [x1, y1, z1], [x1, y1, z0], [x0, y1, z0]); // top (+y)
  quad(p, [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]); // south (+z)
  quad(p, [x1, y0, z0], [x0, y0, z0], [x0, y1, z0], [x1, y1, z0]); // north (-z)
  quad(p, [x1, y0, z1], [x1, y0, z0], [x1, y1, z0], [x1, y1, z1]); // east (+x)
  quad(p, [x0, y0, z0], [x0, y0, z1], [x0, y1, z1], [x0, y1, z0]); // west (-x)
  if (!opts.onGround) quad(p, [x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1]); // bottom (-y)
  parts.push(p); return p;
}
// 12 cm curb + sidewalk slab, 8 m north of the office spawn (x 168, z 246)
box(MAT.sidewalk, 150, 186, G, G + 0.12, 226, 238, { onGround: true, name: 'sidewalk' });
// separate curb strip along the south edge so the curb face carries the curb material
parts.at(-1).curbFaceSouth = true;
// 6-step stair (16 cm rise, 30 cm run) climbing north, then a platform at +0.96 m
for (let k = 0; k < 6; k++) box(MAT.paving_slab, 128, 134, G, G + 0.16 * (k + 1), 220 - 0.3 * (k + 1), 220 - 0.3 * k, { onGround: true, name: 'stairs' });
box(MAT.paving_slab, 128, 134, G, G + 0.96, 208, 218.2, { onGround: true, name: 'platform' });
// building block (east of the office, centroid in c_3_3) with a roof
box(MAT.building, 192, 212, G, G + 12, 200, 216, { onGround: true, name: 'building' });
// markers, visual only (allowed by D5: visual without collision)
box(MAT.wall, 239, 241, G, G + 20, 9, 11, { onGround: true, visualOnly: true, name: 'marker_ne', color: [0.9, 0.1, 0.1] });
box(MAT.wall, 9, 11, G, G + 10, 239, 241, { onGround: true, visualOnly: true, name: 'marker_sw', color: [0.1, 0.2, 0.9] });
// pole: visual cylinder + PRIM cylinder in collision
const POLE = { x: 162, z: 242, r: 0.08, h: 4 };
{
  const p = { mat: MAT.pole, pos: [], idx: [], name: 'pole' }; const n = 12;
  for (let k = 0; k < n; k++) {
    const a0 = (k / n) * Math.PI * 2, a1 = ((k + 1) / n) * Math.PI * 2;
    const A = [POLE.x + POLE.r * Math.cos(a0), POLE.z - POLE.r * Math.sin(a0)], B = [POLE.x + POLE.r * Math.cos(a1), POLE.z - POLE.r * Math.sin(a1)];
    quad(p, [A[0], G, A[1]], [B[0], G, B[1]], [B[0], G + POLE.h, B[1]], [A[0], G + POLE.h, A[1]]);
  }
  parts.push(p);
}
// ground: one grid per collision tile so seams share bitwise-identical vertices
const N = 8; // quads per collision tile edge (7.8125 m, exact in float32)
function groundPart(ci, cj) {
  const p = { mat: MAT.road, pos: [], idx: [], name: 'ground', tile: [ci, cj], triMats: [] };
  const x0 = ci * COL, z0 = cj * COL, s = COL / N;
  for (let b = 0; b <= N; b++) for (let a = 0; a <= N; a++) p.pos.push(x0 + a * s, G, z0 + b * s);
  for (let b = 0; b < N; b++) for (let a = 0; a < N; a++) {
    const v = b * (N + 1) + a;
    p.idx.push(v, v + N + 1, v + 1, v + 1, v + N + 1, v + N + 2); // CCW seen from +y (normal up)
    const cx = x0 + (a + 0.5) * s, cz = z0 + (b + 0.5) * s;
    const m = cx < 60 || cz < 60 ? MAT.grass : MAT.road;
    p.triMats.push(m, m);
  }
  return p;
}
const grounds = [];
for (let cj = 0; cj < L0 / COL; cj++) for (let ci = 0; ci < L0 / COL; ci++) grounds.push(groundPart(ci, cj));

function triMaterials(p) {
  const n = p.idx.length / 3;
  if (p.triMats) return p.triMats;
  const out = new Array(n).fill(p.mat);
  if (p.curbFaceSouth) out[2] = out[3] = MAT.curb; // second quad = south face (towards the spawn)
  if (p.name === 'building') { out[0] = out[1] = MAT.roof; }
  return out;
}

// ---------------------------------------------------------------- .col writer (RAW, v1.0)
function align8(n) { return (n + 7) & ~7; }
export function writeCol({ i, j, positions, indices, materials, prims, flags, meta }) {
  const metaBytes = Buffer.from(JSON.stringify(meta), 'utf8');
  const primBuf = Buffer.alloc(prims.length * 48);
  prims.forEach((p, k) => {
    const o = k * 48; primBuf.writeUInt8(p.kind, o); primBuf.writeUInt8(p.material, o + 1); primBuf.writeUInt16LE(0, o + 2);
    p.center.forEach((v, q) => primBuf.writeFloatLE(v, o + 4 + 4 * q));
    p.rotation.forEach((v, q) => primBuf.writeFloatLE(v, o + 16 + 4 * q));
    p.params.forEach((v, q) => primBuf.writeFloatLE(v, o + 32 + 4 * q));
  });
  const sections = [
    ['POSN', Buffer.from(positions.buffer, positions.byteOffset, positions.byteLength), positions.length / 3, 12],
    ['INDX', Buffer.from(indices.buffer, indices.byteOffset, indices.byteLength), indices.length, 4],
    ['MATL', Buffer.from(materials), materials.length, 1],
    ['PRIM', primBuf, prims.length, 48],
    ['META', metaBytes, metaBytes.length, 1],
  ];
  const tableEnd = 96 + sections.length * 24;
  let off = align8(tableEnd);
  const placed = sections.map(([tag, buf, count, stride]) => { const e = { tag, buf, count, stride, offset: off }; off = align8(off + buf.length); return e; });
  const total = off;
  const out = Buffer.alloc(total);
  out.write('TCOL', 0, 'latin1'); out.writeUInt16LE(1, 4); out.writeUInt16LE(0, 6); out.writeUInt32LE(96, 8); out.writeUInt32LE(flags, 12);
  out.writeDoubleLE(i * COL, 16); out.writeDoubleLE(0, 24); out.writeDoubleLE(j * COL, 32);
  out.writeInt32LE(i, 40); out.writeInt32LE(j, 44);
  const mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
  for (let k = 0; k < positions.length; k++) { mn[k % 3] = Math.min(mn[k % 3], positions[k]); mx[k % 3] = Math.max(mx[k % 3], positions[k]); }
  for (const p of prims) { const r = Math.max(...p.params.slice(0, 3)) + (p.kind === 1 ? 0 : p.params[1]); for (let q = 0; q < 3; q++) { mn[q] = Math.min(mn[q], p.center[q] - r); mx[q] = Math.max(mx[q], p.center[q] + r); } }
  for (let q = 0; q < 3; q++) { out.writeFloatLE(mn[q], 48 + 4 * q); out.writeFloatLE(mx[q], 60 + 4 * q); }
  out.writeUInt32LE(positions.length / 3, 72); out.writeUInt32LE(indices.length / 3, 76); out.writeUInt32LE(prims.length, 80);
  out.writeUInt16LE(sections.length, 84); out.writeUInt16LE(1, 86); out.writeUInt32LE(total, 88); out.writeUInt32LE(0, 92);
  placed.forEach((e, k) => {
    const o = 96 + k * 24; out.write(e.tag, o, 'latin1'); out.writeUInt16LE(0, o + 4); out.writeUInt16LE(0, o + 6);
    out.writeUInt32LE(e.offset, o + 8); out.writeUInt32LE(e.buf.length, o + 12); out.writeUInt32LE(e.count, o + 16); out.writeUInt32LE(e.stride, o + 20);
    e.buf.copy(out, e.offset);
  });
  out.writeUInt32LE(zlib.crc32(out) >>> 0, 92);
  return out;
}

// ---------------------------------------------------------------- build collision tiles
const sha256 = (b) => crypto.createHash('sha256').update(b).digest('hex');
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(path.join(OUT, 'visual/0'), { recursive: true });
fs.mkdirSync(path.join(OUT, 'collision/t0_0_0'), { recursive: true });
const built = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
const manifest = {
  format: 'turku3d-manifest', version: 1, built, code: 'dev-tiles', origin_en: [23459500.0, 6704500.0, 0.0], crs: 'EPSG:3877+3900',
  grid: { visual_l0: 250.0, collision: 62.5, levels: 4 }, materials_version: 1, col_version: [1, 0], areas: ['dev-tiles'],
  tileset: null, visual: {}, collision: {},
};
const centroidTile = (p) => {
  let sx = 0, sz = 0; const n = p.pos.length / 3;
  for (let k = 0; k < n; k++) { sx += p.pos[3 * k]; sz += p.pos[3 * k + 2]; }
  return [Math.floor(sx / n / COL), Math.floor(sz / n / COL)];
};
for (const gp of grounds) {
  const [i, j] = gp.tile;
  const ox = i * COL, oz = j * COL;
  const members = [gp, ...parts.filter((p) => !p.visualOnly && p.name !== 'pole' && centroidTile(p).join() === `${i},${j}`)];
  const pos = [], idx = [], mats = [];
  for (const p of members) {
    const base = pos.length / 3;
    for (let k = 0; k < p.pos.length; k += 3) pos.push(p.pos[k] - ox, p.pos[k + 1], p.pos[k + 2] - oz);
    for (const v of p.idx) idx.push(base + v);
    mats.push(...triMaterials(p));
  }
  const prims = [];
  if (Math.floor(POLE.x / COL) === i && Math.floor(POLE.z / COL) === j)
    prims.push({ kind: 3, material: MAT.pole, center: [POLE.x - ox, G + POLE.h / 2, POLE.z - oz], rotation: [0, 0, 0, 1], params: [POLE.h / 2, POLE.r, 0, 0] });
  const hasCurb = mats.includes(MAT.curb);
  const border = i === 0 || j === 0 || i === 3 || j === 3;
  const flags = (hasCurb ? 1 : 0) | (border ? 2 : 0);
  const id = `c_${i}_${j}`;
  const buf = writeCol({ i, j, positions: new Float32Array(pos), indices: new Uint32Array(idx), materials: Uint8Array.from(mats), prims, flags,
    meta: { tile: id, built, code: 'dev-tiles', notes: 'procedural dummy tile from game/scripts/make-dev-tiles.mjs' } });
  const url = `collision/t0_0_0/${id}.col`;
  fs.writeFileSync(path.join(OUT, url), buf);
  const ys = pos.filter((_, k) => k % 3 === 1);
  manifest.collision[id] = { url, sha256: sha256(buf), bytes: buf.length, tris: idx.length / 3, prims: prims.length, h: [Math.min(...ys), Math.max(...ys)], flags };
}

// ---------------------------------------------------------------- visual GLB
await MeshoptEncoder.ready;
const doc = new Document();
const buffer = doc.createBuffer();
const meshopt = doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.FILTER });
const basisu = doc.createExtension(KHRTextureBasisu).setRequired(true);
// ETC1S ground texture: 256x256 procedural asphalt-ish noise with a 1 m grid line (texture repeats every 4 m)
const TW = 256, rgba = new Uint8Array(TW * TW * 4);
for (let y = 0; y < TW; y++) for (let x = 0; x < TW; x++) {
  const n = (Math.sin(x * 12.9898 + y * 78.233) * 43758.5453) % 1; const v = 200 + Math.abs(n) * 40;
  const line = x % 64 < 2 || y % 64 < 2 ? 0.75 : 1;
  rgba.set([v * line, v * line, v * line, 255], (y * TW + x) * 4);
}
const ktx = await encodeToKTX2(new Uint8Array(1), { isUASTC: false, generateMipmap: true, qualityLevel: 128, isSetKTX2SRGBTransferFunc: true,
  imageDecoder: async () => ({ width: TW, height: TW, data: rgba }) });
const tex = doc.createTexture('ground_etc1s').setMimeType('image/ktx2').setImage(new Uint8Array(ktx)).setURI('ground.ktx2');
const sceneNode = doc.createNode('t0_0_0').setTranslation([0, 0, 0]).setExtras({ tile: 't0_0_0', objects: [] });
const scene = doc.createScene('t0_0_0').addChild(sceneNode);
const matCache = new Map();
function material(key, color, textured) {
  if (matCache.has(key)) return matCache.get(key);
  const m = doc.createMaterial(key).setBaseColorFactor([...color, 1]).setRoughnessFactor(0.85).setMetallicFactor(key.startsWith('m25') ? 0.6 : 0);
  if (textured) m.setBaseColorTexture(tex);
  matCache.set(key, m); return m;
}
let visTris = 0, ymin = Infinity, ymax = -Infinity;
function addMesh(name, pos, idx, mat, color, textured) {
  const P = new Float32Array(pos);
  const normals = new Float32Array(P.length);
  for (let k = 0; k < idx.length; k += 3) { // flat-ish: accumulate face normals
    const [a, b, c] = [idx[k], idx[k + 1], idx[k + 2]].map((v) => v * 3);
    const u = [P[b] - P[a], P[b + 1] - P[a + 1], P[b + 2] - P[a + 2]], w = [P[c] - P[a], P[c + 1] - P[a + 1], P[c + 2] - P[a + 2]];
    const nx = u[1] * w[2] - u[2] * w[1], ny = u[2] * w[0] - u[0] * w[2], nz = u[0] * w[1] - u[1] * w[0];
    for (const v of [a, b, c]) { normals[v] += nx; normals[v + 1] += ny; normals[v + 2] += nz; }
  }
  for (let k = 0; k < normals.length; k += 3) { const l = Math.hypot(normals[k], normals[k + 1], normals[k + 2]) || 1; normals[k] /= l; normals[k + 1] /= l; normals[k + 2] /= l; }
  const prim = doc.createPrimitive()
    .setAttribute('POSITION', doc.createAccessor().setType('VEC3').setArray(P).setBuffer(buffer))
    .setAttribute('NORMAL', doc.createAccessor().setType('VEC3').setArray(normals).setBuffer(buffer))
    .setIndices(doc.createAccessor().setType('SCALAR').setArray(new Uint32Array(idx)).setBuffer(buffer))
    .setMaterial(material(`m${mat}_${name}`, color, textured));
  if (textured) {
    const uv = new Float32Array((P.length / 3) * 2);
    for (let k = 0; k < P.length / 3; k++) { uv[2 * k] = P[3 * k] / 4; uv[2 * k + 1] = P[3 * k + 2] / 4; }
    prim.setAttribute('TEXCOORD_0', doc.createAccessor().setType('VEC2').setArray(uv).setBuffer(buffer));
  }
  for (let k = 1; k < P.length; k += 3) { ymin = Math.min(ymin, P[k]); ymax = Math.max(ymax, P[k]); }
  visTris += idx.length / 3;
  sceneNode.addChild(doc.createNode(name).setMesh(doc.createMesh(name).addPrimitive(prim)));
}
// ground split by material (tile-local == world for t0_0_0)
for (const m of [MAT.road, MAT.grass]) {
  const pos = [], idx = [];
  for (const gp of grounds) {
    const base = pos.length / 3; pos.push(...gp.pos);
    for (let t = 0; t < gp.idx.length / 3; t++) if (gp.triMats[t] === m) idx.push(base + gp.idx[3 * t], base + gp.idx[3 * t + 1], base + gp.idx[3 * t + 2]);
  }
  addMesh(m === MAT.road ? 'ground_road' : 'ground_grass', pos, idx, m, COLOR[m], true);
}
for (const p of parts) addMesh(p.name, p.pos, p.idx, p.mat, p.color ?? COLOR[p.mat], false);
void meshopt; void basisu;
const io = new NodeIO().registerExtensions([EXTMeshoptCompression, KHRTextureBasisu]).registerDependencies({ 'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder });
const glb = Buffer.from(await io.writeBinary(doc));
const vurl = 'visual/0/t0_0_0.glb';
fs.writeFileSync(path.join(OUT, vurl), glb);
manifest.visual.t0_0_0 = { url: vurl, sha256: sha256(glb), bytes: glb.length, level: 0, tris: visTris, h: [ymin, ymax] };
void scene;

// ---------------------------------------------------------------- tileset.json (3D Tiles frame: Z-up, Y = north)
const box3 = (i, j, s, h0, h1) => { const hz = Math.max(1, (h1 - h0) / 2); return [(i + 0.5) * s, -(j + 0.5) * s, (h0 + h1) / 2, s / 2, 0, 0, 0, s / 2, 0, 0, 0, hz]; };
const tileset = {
  asset: { version: '1.0', generator: 'turku3d dev-tiles', extras: { frame: 'turku3d-local-zup', origin_en: [23459500, 6704500, 0], crs: 'EPSG:3877+3900' } },
  geometricError: 512,
  root: {
    boundingVolume: { box: box3(0, 0, L0, ymin, ymax) }, geometricError: 512, refine: 'REPLACE',
    children: [{ boundingVolume: { box: box3(0, 0, L0, ymin, ymax) }, geometricError: 0, content: { uri: vurl }, extras: { id: 't0_0_0' } }],
  },
};
const tsBuf = Buffer.from(JSON.stringify(tileset, null, 2));
fs.writeFileSync(path.join(OUT, 'tileset.json'), tsBuf);
manifest.tileset = { url: 'tileset.json', sha256: sha256(tsBuf), bytes: tsBuf.length };
const sortObj = (o) => Object.fromEntries(Object.entries(o).sort(([a], [b]) => (a < b ? -1 : 1)));
manifest.collision = sortObj(manifest.collision);
fs.writeFileSync(path.join(OUT, 'manifest.json.tmp'), JSON.stringify(manifest, null, 2));
fs.renameSync(path.join(OUT, 'manifest.json.tmp'), path.join(OUT, 'manifest.json'));
console.log(`dev tiles -> ${OUT}: GLB ${glb.length} B (${visTris} tris), ${Object.keys(manifest.collision).length} .col tiles, ktx2 ${ktx.length} B`);
