// Convert a permissively licensed source car (glTF/GLB) into the fleet's articulated LOD format.
//   node scripts/import-fleet-gltf.mjs <models.json> <id> [--work DIR]
// The source stays outside git (~/turku3d/loops/carmodels/<id>/). Output: three raw LODs in the work directory,
// packed by prepare-fleet.mjs into game/public/assets/cars/<id>/, plus the runtime registry entry.
// Steps: bake world transforms; map source materials to fleet roles (paint is tintable, badges are dropped and
// plates become blank generic plates); sample base-colour textures into vertex colours (no image enters the
// runtime asset); orient to +Z forward / Y up / metres with tyre contact at Y=0; split the four wheels at their
// hubs; simplify each LOD to its budget with meshoptimizer.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';
import { Document, NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { MeshoptSimplifier } from 'meshoptimizer';

const here = path.dirname(fileURLToPath(import.meta.url)), repo = path.resolve(here, '../..');
const [configPath, id] = process.argv.slice(2);
if (!configPath || !id) throw Error('Usage: import-fleet-gltf.mjs <models.json> <id>');
const all = JSON.parse(fs.readFileSync(configPath, 'utf8'));
const cfg = all.models.find(m => m.id === id);
if (!cfg) throw Error(`No model ${id} in ${configPath}`);
const expand = p => p.replace(/^~(?=\/)/, os.homedir());
const source = expand(cfg.source);
const work = expand(process.argv.includes('--work') ? process.argv[process.argv.indexOf('--work') + 1] : path.join(path.dirname(source), 'build'));
fs.mkdirSync(work, { recursive: true });
await MeshoptSimplifier.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS);
const doc = await io.read(source);

// ---- Material roles -------------------------------------------------------------------------------------------
// Order matters: the first matching rule wins. Model rules (cfg.roles) come before these defaults. The key tested is
// "<node name>|<material name>" in lower case.
const DEFAULT_ROLES = [
  [/shadow|invisible|hemi|logo|emblem|badge|lettering|decal|sticker|brand|fs_vector_bulbs|_ao_plane|groundplane/, 'drop'],
  [/plate|licen[cs]e|numbers|kenteken|registr/, 'plate'],
  [/blinker|indicator|signal|turn.?light|orange|amber/, 'indicator'],
  [/(?<![a-z])tail|brake.?light|rear.?light|reverse|back.?light|stop.?light|taillight|reflector.*tail|rearlamp/, 'lampTail'],
  [/head.?light|headlamp|hl.?front|front.?light|farol|fog/, 'lampFront'],
  [/light.?glass|lightglass|lens|klosz|lamp|light/, 'lampAny'],
  [/glass|window|windshield|windscreen|\bwin\b|win_|vidrio|scheibe/, 'glass'],
  [/caliper|brembo/, 'trim'],
  [/steer|chair|seat/, 'interior'],
  [/tyre|tire|rubber|reifen|pneu/, 'tyre'],
  [/(?<!t)rim|wheel|felge|alloy|hubcap|disk|disc|brake/, 'rim'],
  [/interior|seat|salon|leather|dash|carpet|belt|steer|fabric|gauge|speaker|screen|furniture|pedal|engine|shifter|ignition|cabin|inside|door.?card|knob/, 'interior'],
  [/chrome|mirror|miror|metal|steel|exhaust/, 'chrome'],
  [/paint|carpaint|body|(?<!b)lack|color|colour|karosserie/, 'paint'],
];
const ROLE = { paint: 1, trim: 2, chrome: 3, glass: 4, tyre: 5, rim: 6, interior: 7, plate: 8,
  lampFront: 9, lampTail: 10, indicator: 11, lampAny: 12, livery: 13 };
const isLamp = role => role >= ROLE.lampFront && role <= ROLE.indicator;
const modelRules = (cfg.roles ?? []).map(([re, role]) => [new RegExp(re, 'i'), role]);
// Part names on nodes are the strongest hint (a "wheels" node holds painted rims; an "interior" node holds paint
// too); materials decide the rest. Generic names fall back to trim.
const NODE_ROLES = ['interior', 'indicator', 'lampTail', 'lampFront', 'tyre', 'rim'].map(r => DEFAULT_ROLES.find(([, role]) => role === r))
  .map(([re, role]) => [role === 'rim' ? /wheel|(?<!t)rim|felge/ : role === 'interior' ? /interior|(^|[^a-z])seats?([^a-z_]|$)|salon|dash|steer|furniture|cabin|inside|engine/ : re, role]);
function roleOf(node, materialName) {
  const key = `${node}|${materialName}`;
  for (const [re, role] of modelRules) if (re.test(key)) return role;
  if (DEFAULT_ROLES[0][0].test(key)) return 'drop';
  for (const [re, role] of NODE_ROLES) if (re.test(node)) return role;
  for (const [re, role] of DEFAULT_ROLES) if (re.test(materialName)) return role;
  return 'trim';
}

// ---- Textures → vertex colour ----------------------------------------------------------------------------------
const images = new Map();
async function decode(texture) {
  if (!texture) return null;
  let img = images.get(texture);
  if (!img) {
    // A small box-filtered copy: per-vertex samples should carry the average colour of a region, not a texel.
    const { data, info } = await sharp(Buffer.from(texture.getImage())).resize(128, 128, { fit: 'fill', kernel: 'cubic' })
      .removeAlpha().raw().toBuffer({ resolveWithObject: true });
    img = { data, w: info.width, h: info.height }; images.set(texture, img);
  }
  return img;
}
const toLinear = c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4;
function sample(img, u, v) {
  u -= Math.floor(u); v -= Math.floor(v);
  const x = u * img.w - .5, y = v * img.h - .5, x0 = Math.floor(x), y0 = Math.floor(y), fx = x - x0, fy = y - y0;
  const at = (xx, yy) => ((((yy % img.h) + img.h) % img.h) * img.w + (((xx % img.w) + img.w) % img.w)) * 3;
  const out = [0, 0, 0];
  for (const [xx, yy, w] of [[x0, y0, (1 - fx) * (1 - fy)], [x0 + 1, y0, fx * (1 - fy)], [x0, y0 + 1, (1 - fx) * fy], [x0 + 1, y0 + 1, fx * fy]]) {
    const o = at(xx, yy); for (let k = 0; k < 3; k++) out[k] += img.data[o + k] / 255 * w;
  }
  return out; // sRGB
}
async function materialInfo(material) {
  if (!material) return { name: '', factor: [.6, .6, .6, 1], texture: null, rough: .6, metal: 0, glass: false, uv: 0, xf: null };
  const sg = material.getExtension('KHR_materials_pbrSpecularGlossiness');
  const factor = sg ? sg.getDiffuseFactor() : material.getBaseColorFactor();
  const texture = sg ? sg.getDiffuseTexture() : material.getBaseColorTexture();
  const info = sg ? sg.getDiffuseTextureInfo() : material.getBaseColorTextureInfo();
  const xf = info?.getExtension('KHR_texture_transform');
  const transmission = material.getExtension('KHR_materials_transmission')?.getTransmissionFactor() ?? 0;
  return { name: material.getName() ?? '', factor, texture: await decode(texture), uv: info?.getTexCoord() ?? 0,
    xf: xf ? { o: xf.getOffset(), s: xf.getScale(), r: xf.getRotation() } : null,
    rough: sg ? 1 - sg.getGlossinessFactor() : material.getRoughnessFactor(), metal: sg ? 0 : material.getMetallicFactor(),
    glass: transmission > .2 || (material.getAlphaMode() === 'BLEND' && factor[3] < .9) };
}

// ---- Bake the scene into one triangle soup ---------------------------------------------------------------------
function mul(m, x, y, z, w) { return [m[0]*x+m[4]*y+m[8]*z+m[12]*w, m[1]*x+m[5]*y+m[9]*z+m[13]*w, m[2]*x+m[6]*y+m[10]*z+m[14]*w]; }
const tris = []; // {p:[9], n:[9], c:[9], role, roleName, key}
const materials = new Map();
const keysSeen = new Map();
for (const node of doc.getRoot().listNodes()) {
  const mesh = node.getMesh(); if (!mesh) continue;
  const m = node.getWorldMatrix();
  for (const prim of mesh.listPrimitives()) {
    if (prim.getMode() !== 4) continue;
    const material = prim.getMaterial();
    let info = materials.get(material); if (!info) { info = await materialInfo(material); materials.set(material, info); }
    const key = `${node.getName()}|${info.name}`.toLowerCase();
    let role = roleOf(node.getName().toLowerCase(), info.name.toLowerCase());
    if (role !== 'drop' && info.glass && !/^lamp|indicator|interior/.test(role)) role = 'glass';
    keysSeen.set(key, role);
    if (role === 'drop') continue;
    const pos = prim.getAttribute('POSITION'), nor = prim.getAttribute('NORMAL'), uv = prim.getAttribute(`TEXCOORD_${info.uv}`);
    // CARLA (UE) vertex colours are paint masks, not colours.
    const col = cfg.ignoreVertexColor ? null : prim.getAttribute('COLOR_0'), idx = prim.getIndices();
    // Skinned sources (CARLA): a vertex belongs to the joint with the largest weight; Wheel_* joints mark wheels.
    const joints = prim.getAttribute('JOINTS_0'), weights = prim.getAttribute('WEIGHTS_0'), jointNames = node.getSkin()?.listJoints().map(j => j.getName()) ?? [];
    const D = [];
    const count = idx ? idx.getCount() : pos.getCount();
    const P = [], N = [], C = [], t3 = [0, 0, 0], t2 = [0, 0], t4 = [0, 0, 0, 0];
    for (let v = 0; v < pos.getCount(); v++) {
      pos.getElement(v, t3); P.push(mul(m, t3[0], t3[1], t3[2], 1));
      if (nor) { nor.getElement(v, t3); const n = mul(m, t3[0], t3[1], t3[2], 0), l = Math.hypot(...n) || 1; N.push(n.map(a => a / l)); } else N.push(null);
      let c = [1, 1, 1];
      if (info.texture && uv) {
        uv.getElement(v, t2); let [u, w] = t2;
        if (info.xf) { const { o, s, r } = info.xf, cs = Math.cos(r), sn = Math.sin(r); u *= s[0]; w *= s[1]; [u, w] = [cs*u + sn*w + o[0], -sn*u + cs*w + o[1]]; }
        c = sample(info.texture, u, w);
      }
      c = c.map(toLinear).map((a, k) => a * info.factor[k]);
      if (col) { col.getElement(v, t4); c = c.map((a, k) => a * t4[k]); }
      C.push(c);
      if (joints && weights && jointNames.length) {
        const j4 = [0, 0, 0, 0], w4 = [0, 0, 0, 0]; joints.getElement(v, j4); weights.getElement(v, w4);
        D.push(jointNames[j4[w4.indexOf(Math.max(...w4))]] ?? '');
      }
    }
    for (let i = 0; i < count; i += 3) {
      const ids = idx ? [idx.getScalar(i), idx.getScalar(i + 1), idx.getScalar(i + 2)] : [i, i + 1, i + 2];
      const p = ids.flatMap(v => P[v]);
      let n;
      if (ids.every(v => N[v])) n = ids.flatMap(v => N[v]);
      else {
        const ax = p[3]-p[0], ay = p[4]-p[1], az = p[5]-p[2], bx = p[6]-p[0], by = p[7]-p[1], bz = p[8]-p[2];
        const f = [ay*bz-az*by, az*bx-ax*bz, ax*by-ay*bx], l = Math.hypot(...f) || 1; n = [...f, ...f, ...f].map(a => a / l);
      }
      const dominant = D.length ? ids.map(v => D[v]) : null;
      const wheel = dominant && /^wheel_/i.test(dominant[0]) && dominant.every(d => d === dominant[0]) ? dominant[0] : null;
      const triRole = wheel && !['tyre', 'glass', 'drop'].includes(role) ? 'rim' : role;
      tris.push({ p, n, c: ids.flatMap(v => C[v]), role: triRole, info, key, wheel });
    }
  }
}
if (!tris.length) throw Error('No triangles');

// Atlas liveries: a body texture holds paint and trim together. Triangles whose sampled colour matches the
// declared paint colour become tintable paint.
for (const rule of cfg.atlasPaint ?? []) {
  const re = new RegExp(rule.material, 'i'), target = rule.rgb.map(a => toLinear(a / 255)), tol = rule.tol ?? .08;
  for (const t of tris) {
    if (!re.test(t.info.name) || t.role === 'glass') continue;
    const mean = [0, 1, 2].map(k => (t.c[k] + t.c[k + 3] + t.c[k + 6]) / 3 / Math.max(1e-3, t.info.factor[k]));
    if (Math.hypot(...mean.map((a, k) => a - target[k])) < tol) t.role = 'paint';
  }
}

// Emergency, taxi and transit liveries keep their sampled colours; they are not tinted by the traffic paint.
if (cfg.livery) for (const t of tris) if (t.role === 'paint') {
  t.role = 'livery';
  // A fixed body colour (linear, e.g. CARLA's "Base Color" parameter) multiplies the sampled texture.
  // paintReplace: the texture is only a mask (CARLA), so the parameter colour replaces it.
  if (cfg.paintLinear) for (let i = 0; i < 9; i++) t.c[i] = cfg.paintReplace ? cfg.paintLinear[i % 3] : t.c[i] * cfg.paintLinear[i % 3];
}
// Per-part colour replacement (linear), e.g. police door shells that carry the civilian body material.
for (const rule of cfg.recolor ?? []) { const re = new RegExp(rule.key, 'i');
  for (const t of tris) if (re.test(t.key)) { t.c = t.c.map((_, i) => rule.rgbLinear[i % 3]); if (rule.role) t.role = rule.role; } }

// ---- Orientation, scale, ground ----------------------------------------------------------------------------------
const box = (list, filter = () => true) => {
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const t of list) if (filter(t)) for (let i = 0; i < 9; i++) { lo[i % 3] = Math.min(lo[i % 3], t.p[i]); hi[i % 3] = Math.max(hi[i % 3], t.p[i]); }
  return { lo, hi, size: hi.map((h, k) => h - lo[k]) };
};
const transform = (fn) => { for (const t of tris) for (let i = 0; i < 9; i += 3) {
  const [x, y, z] = fn(t.p[i], t.p[i+1], t.p[i+2], 1), [a, b, c] = fn(t.n[i], t.n[i+1], t.n[i+2], 0);
  t.p.splice(i, 3, x, y, z); t.n.splice(i, 3, a, b, c);
} };
const exterior = t => t.role !== 'interior';
if (cfg.up === '+z') transform((x, y, z) => [x, z, -y]);
let b = box(tris, exterior);
if (b.size[0] > b.size[2]) transform((x, y, z) => [-z, y, x]);
b = box(tris, exterior);
const centroidZ = role => { let s = 0, n = 0; for (const t of tris) if (t.role === role) { s += t.p[2] + t.p[5] + t.p[8]; n += 3; } return n ? s / n : null; };
let forward = cfg.forward;
if (!forward) {
  const front = centroidZ('lampFront'), tail = centroidZ('lampTail'), mid = (b.lo[2] + b.hi[2]) / 2;
  forward = front !== null && tail !== null ? (front > tail ? '+z' : '-z') : front !== null ? (front > mid ? '+z' : '-z')
    : tail !== null ? (tail < mid ? '+z' : '-z') : '+z';
}
if (forward === '-z') transform((x, y, z) => [-x, y, -z]);
// mirror: a right-hand-drive source becomes left-hand drive like Finnish vehicles (the driver on the left, a bus's
// doors on the kerb side); mirroring X turns each triangle over, so two corners swap to keep the front face out.
if (cfg.mirror) {
  transform((x, y, z) => [-x, y, z]);
  for (const t of tris) for (const a of [t.p, t.n, t.c]) for (let k = 0; k < 3; k++) [a[3 + k], a[6 + k]] = [a[6 + k], a[3 + k]];
}
b = box(tris, exterior);
const scale = cfg.length ? cfg.length / b.size[2] : 1;
const cx = (b.lo[0] + b.hi[0]) / 2, cz = (b.lo[2] + b.hi[2]) / 2;
transform((x, y, z, w) => w ? [(x - cx) * scale, (y - b.lo[1]) * scale, (z - cz) * scale] : [x, y, z]);

// lampAny: front/tail by position; indicators: side by X (+X is the car's left when it faces +Z, Y up).
const ctr = t => [(t.p[0]+t.p[3]+t.p[6])/3, (t.p[1]+t.p[4]+t.p[7])/3, (t.p[2]+t.p[5]+t.p[8])/3];
for (const t of tris) if (t.role === 'lampAny' || (cfg.lampsByPosition && (t.role === 'lampFront' || t.role === 'lampTail')))
  t.role = ctr(t)[2] > 0 ? 'lampFront' : 'lampTail';

// ---- Wheels ------------------------------------------------------------------------------------------------------
const H = b.size[1] * scale;
// Wheel candidates sit low: door seals and window rubbers share "rubber" materials with tyres.
// Skinned sources mark wheels by bone; otherwise tyre/rim materials that sit low.
const boned = tris.filter(t => t.wheel);
const wheelTris = boned.length ? boned : tris.filter(t => (t.role === 'tyre' || t.role === 'rim') && ctr(t)[1] < H * (cfg.wheelMaxY ?? .5));
if (!wheelTris.length) throw Error('No tyre/rim triangles: add roles for this model');
// Axles: dense Z bands of wheel triangles (2 cm bins); sparse seals and pipes between them fall under the threshold.
const zs = wheelTris.map(t => ctr(t)[2]), z0 = zs.reduce((a, c) => Math.min(a, c)), bins = new Float64Array(Math.ceil((zs.reduce((a, c) => Math.max(a, c)) - z0) / .02) + 1);
for (const z of zs) bins[Math.floor((z - z0) / .02)]++;
const smooth = Array.from(bins, (_, i) => (bins[i - 2] ?? 0) + (bins[i - 1] ?? 0) + bins[i] + (bins[i + 1] ?? 0) + (bins[i + 2] ?? 0));
const peak = smooth.reduce((a, c) => Math.max(a, c)), axles = [];
smooth.forEach((v, i) => { if (v < peak * (cfg.axleThreshold ?? .12)) return; const z = z0 + i * .02;
  const last = axles[axles.length - 1]; if (last && z - last[1] <= .08) last[1] = z + .02; else axles.push([z, z + .02]); });
// Axle bands carry most of the wheel mass; bumpers or pipes that share a "wheels and parts" material do not.
const mass = ([a, c]) => zs.filter(z => z >= a && z <= c).length;
const wide = axles.filter(([a, c]) => c - a > .18), heaviest = wide.reduce((m, a) => Math.max(m, mass(a)), 0);
// A wheel band is round in side view (Y extent close to Z extent) and reaches the lowest wheel point.
const lowest = wheelTris.reduce((m, t) => Math.min(m, t.p[1], t.p[4], t.p[7]), Infinity);
const round_ = ([a, c]) => { const bb = box(wheelTris, t => { const z = ctr(t)[2]; return z >= a && z <= c; });
  return Math.min(bb.size[1], bb.size[2]) / Math.max(bb.size[1], bb.size[2]) > .6 && bb.lo[1] - lowest < .25 * bb.size[1]; };
let major = wide.filter(a => mass(a) >= heaviest * .3 && round_(a));
if (major.length < 2) major = wide.filter(a => mass(a) >= heaviest * .3);
if (major.length < 2) throw Error(`Expected two axles, found ${JSON.stringify(axles)}`);
const front = major[major.length - 1], rear = major[0];
// Skinned sources: each Wheel_* bone is one wheel, so its axle comes from its own triangles, not from Z density
// (chassis parts weighted to a tandem's two bone pairs filled the gap, and the HGV's rear hub fell between its axles).
// A bone's axle is the Z of its contact patch; the front-most and rear-most bone pairs become the four hubs, and
// middle axles (Wheel_Middle_*) stay in the body like those of unskinned sources.
const boneAxles = (() => {
  if (!boned.length) return null;
  const groups = new Map();
  for (const t of boned) { if (!groups.has(t.wheel)) groups.set(t.wheel, []); groups.get(t.wheel).push(t); }
  const contactZ = list => { const low = list.reduce((m, t) => Math.min(m, t.p[1], t.p[4], t.p[7]), Infinity);
    const patch = list.filter(t => Math.min(t.p[1], t.p[4], t.p[7]) < low + .05); return patch.reduce((s, t) => s + ctr(t)[2], 0) / patch.length; };
  const sorted = [...groups].map(([bone, list]) => ({ bone, list, z: contactZ(list) })).sort((a, c) => a.z - c.z);
  const axles = [];
  for (const g of sorted) { const last = axles[axles.length - 1]; if (last && g.z - last.z < .3) last.groups.push(g); else axles.push({ z: g.z, groups: [g] }); }
  if (axles.length < 2) return null;
  const set = axle => ({ bones: new Set(axle.groups.map(g => g.bone)), tris: axle.groups.flatMap(g => g.list) });
  return { front: set(axles[axles.length - 1]), rear: set(axles[0]) };
})();
const hubs = {};
for (const [axle, names, bones] of [[front, ['wheel_FL', 'wheel_FR'], boneAxles?.front], [rear, ['wheel_RL', 'wheel_RR'], boneAxles?.rear]]) {
  const onAxle = bones ? bones.tris : wheelTris.filter(t => { const z = ctr(t)[2]; return z >= axle[0] - .03 && z <= axle[1] + .03; });
  const xs = onAxle.map(t => ctr(t)[0]), mid = (xs.reduce((a, c) => Math.min(a, c)) + xs.reduce((a, c) => Math.max(a, c))) / 2;
  for (const [name, side] of [[names[0], -1], [names[1], 1]]) {
    // Outboard triangles only: axle beams, differentials and suspension parts sit near the centreline.
    const sided = onAxle.filter(t => Math.sign(ctr(t)[0] - mid) === side);
    const reach = sided.reduce((a, t) => Math.max(a, Math.abs(ctr(t)[0] - mid)), 0);
    const members = sided.filter(t => Math.abs(ctr(t)[0] - mid) >= reach * .55);
    const tyres = members.filter(t => t.role === 'tyre');
    const bb = box(tyres.length > 40 ? tyres : members);
    hubs[name] = { hub: [(bb.lo[0] + bb.hi[0]) / 2, (bb.lo[1] + bb.hi[1]) / 2, (bb.lo[2] + bb.hi[2]) / 2],
      radius: Math.min(bb.size[1], bb.size[2]) / 2, width: bb.size[0], side, bones: bones?.bones };
  }
}
// Centre the track on X=0 (mirrors and accessories may make the body box asymmetric).
const trackMid = (hubs.wheel_FL.hub[0] + hubs.wheel_FR.hub[0] + hubs.wheel_RL.hub[0] + hubs.wheel_RR.hub[0]) / 4;
transform((x, y, z, w) => w ? [x - trackMid, y, z] : [x, y, z]);
for (const h of Object.values(hubs)) h.hub[0] -= trackMid;
// Ground: the lowest tyre touches Y=0; then pivots sit at their radius.
const ground = Math.min(...Object.values(hubs).map(h => h.hub[1] - h.radius));
transform((x, y, z, w) => w ? [x, y - ground, z] : [x, y, z]);
for (const h of Object.values(hubs)) h.hub[1] -= ground;
// Symmetric layout: one radius/width, mirrored track per axle (the runtime shares one tyre design per car).
const radius = Object.values(hubs).reduce((s, h) => s + h.radius, 0) / 4;
let width = Object.values(hubs).reduce((s, h) => s + h.width, 0) / 4;
for (const pair of [['wheel_FL', 'wheel_FR'], ['wheel_RL', 'wheel_RR']]) {
  const [l, r] = pair.map(n => hubs[n]), track = (r.hub[0] - l.hub[0]) / 2, z = (l.hub[2] + r.hub[2]) / 2;
  l.hub = [-track, radius, z]; r.hub = [track, radius, z];
}
{
  const debug = {};
  for (const t of tris) {
    const d = debug[t.key] ??= { role: t.role, tris: 0, lo: [Infinity, Infinity, Infinity], hi: [-Infinity, -Infinity, -Infinity] };
    d.tris++; for (let i = 0; i < 9; i++) { d.lo[i % 3] = Math.min(d.lo[i % 3], t.p[i]); d.hi[i % 3] = Math.max(d.hi[i % 3], t.p[i]); }
  }
  for (const [key, role] of keysSeen) if (role === 'drop') debug[key] = { role };
  for (const d of Object.values(debug)) if (d.lo) { d.lo = d.lo.map(a => +a.toFixed(2)); d.hi = d.hi.map(a => +a.toFixed(2)); }
  fs.writeFileSync(path.join(work, 'roles.json'), JSON.stringify(Object.fromEntries(Object.entries(debug).sort()), null, 0).replace(/},"/g, '},\n"'));
}

const ext0 = () => box(tris, exterior);
if (process.env.PROBE) { // PROBE=x,y,z[,r]: which source parts have triangles near a model-space point
  const [px, py, pz, pr = .06] = process.env.PROBE.split(',').map(Number), hits = {};
  for (const t of tris) { const c = ctr(t); if (Math.hypot(c[0]-px, c[1]-py, c[2]-pz) < pr) hits[`${t.key} ${t.role}`] = (hits[`${t.key} ${t.role}`] ?? 0) + 1; }
  console.log(JSON.stringify({ probe: hits }));
}
// ---- Generated liveries (no textures, no logos) --------------------------------------------------------------------
// police-fi: white body, blue belt band and a blue/yellow check row on the flanks, blue light bar on the roof.
// taxi-fi: body keeps its tintable paint, a yellow sign box on the roof.
function addBox([cx, cy, cz], [sx, sy, sz], colour, role) {
  const v = (x, y, z) => [cx + x * sx / 2, cy + y * sy / 2, cz + z * sz / 2];
  for (const [n, quad] of [[[1, 0, 0], [[1,-1,-1],[1,1,-1],[1,1,1],[1,-1,1]]], [[-1, 0, 0], [[-1,-1,1],[-1,1,1],[-1,1,-1],[-1,-1,-1]]],
    [[0, 1, 0], [[-1,1,-1],[-1,1,1],[1,1,1],[1,1,-1]]], [[0, 0, 1], [[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]]], [[0, 0, -1], [[1,-1,-1],[-1,-1,-1],[-1,1,-1],[1,1,-1]]]])
    for (const tri of [[0, 1, 2], [0, 2, 3]]) tris.push({ p: tri.flatMap(i => v(...quad[i])), n: [...n, ...n, ...n], c: [...colour, ...colour, ...colour], role, info: { name: 'generated', factor: [1, 1, 1, 1] }, key: 'generated|livery' });
}
if (cfg.generatedLivery) {
  const E = ext0(), H = E.size[1], roofTris = tris.filter(t => exterior(t) && Math.abs(ctr(t)[0]) < .25 && Math.abs(ctr(t)[2] - (E.lo[2] + E.hi[2]) / 2) < .5);
  const roof = roofTris.reduce((m, t) => Math.max(m, ctr(t)[1]), 0);
  const roofZ = (() => { const top = roofTris.filter(t => ctr(t)[1] > roof - .05); return top.reduce((s, t) => s + ctr(t)[2], 0) / Math.max(1, top.length); })();
  const lin = rgb => rgb.map(a => toLinear(a / 255));
  if (cfg.generatedLivery === 'police-fi') {
    // Stripes and checks are coloured per triangle: split large flank triangles first (longest edge <= 8 cm).
    const split = t => { const P = i => t.p.slice(i * 3, i * 3 + 3), d = (a, c) => Math.hypot(...P(a).map((v, k) => v - P(c)[k]));
      const e = [[0, 1, 2], [1, 2, 0], [2, 0, 1]].sort((x, y) => d(y[0], y[1]) - d(x[0], x[1]))[0];
      if (d(e[0], e[1]) <= .08) return [t];
      const mid = (arr, a, c) => [0, 1, 2].map(k => (arr[a*3+k] + arr[c*3+k]) / 2);
      const pick = (arr, i) => arr.slice(i * 3, i * 3 + 3);
      const mk = (a, b, c) => ({ ...t, p: [...a.p, ...b.p, ...c.p], n: [...a.n, ...b.n, ...c.n], c: [...a.c, ...b.c, ...c.c] });
      const V = i => ({ p: pick(t.p, i), n: pick(t.n, i), c: pick(t.c, i) }), M = { p: mid(t.p, e[0], e[1]), n: mid(t.n, e[0], e[1]), c: mid(t.c, e[0], e[1]) };
      return [...split(mk(V(e[0]), M, V(e[2]))), ...split(mk(M, V(e[1]), V(e[2])))]; };
    for (let i = tris.length - 1; i >= 0; i--) { const t = tris[i];
      if ((t.role === 'paint' || t.role === 'livery') && Math.abs(t.n[0] + t.n[3] + t.n[6]) / 3 > .5) tris.splice(i, 1, ...split(t)); }
    const white = lin([236, 238, 240]), blue = lin([18, 52, 140]), yellow = lin([244, 196, 0]);
    for (const t of tris) {
      if (t.role !== 'paint' && t.role !== 'livery') continue;
      const [, y, z] = ctr(t), side = Math.abs(t.n[0] + t.n[3] + t.n[6]) / 3 > .5, f = y / H;
      let c = white;
      if (side && f > .47 && f < .55) c = blue;
      else if (side && f > .33 && f <= .45) c = (Math.floor(z / .22) + Math.floor((f - .33) / .06)) % 2 ? blue : yellow;
      t.role = 'livery'; for (let i = 0; i < 9; i++) t.c[i] = c[i % 3];
    }
    addBox([0, roof + .05, roofZ], [1.1, .1, .3], blue, 'livery');
  } else if (cfg.generatedLivery === 'taxi-fi') {
    addBox([0, roof + .08, roofZ - .1], [.55, .16, .2], lin([250, 205, 30]), 'livery');
  }
}

// ---- Debadging ----------------------------------------------------------------------------------------------------
// Brand emblems are small separate parts on the centreline at the nose or tail (grille, boot lid, hub caps are
// handled by the wheels' own sampled colour). Connected components per source part are found by shared positions.
{
  const L = ext0().size[2], zMax = ext0().hi[2], zMin = ext0().lo[2];
  const groups = new Map();
  tris.forEach((t, i) => { if (['chrome', 'trim', 'paint', 'livery'].includes(t.role)) (groups.get(t.key) ?? groups.set(t.key, []).get(t.key)).push(i); });
  let dropped = 0;
  for (const list of groups.values()) {
    const parent = new Map(), find = a => { while (parent.get(a) !== a) { parent.set(a, parent.get(parent.get(a))); a = parent.get(a); } return a; };
    const vkey = (t, k) => `${Math.round(t.p[k*3]*2000)},${Math.round(t.p[k*3+1]*2000)},${Math.round(t.p[k*3+2]*2000)}`;
    for (const i of list) { const ks = [0, 1, 2].map(k => vkey(tris[i], k)); for (const k of ks) if (!parent.has(k)) parent.set(k, k);
      parent.set(find(ks[1]), find(ks[0])); parent.set(find(ks[2]), find(ks[0])); }
    const comps = new Map();
    for (const i of list) { const r = find(vkey(tris[i], 0)); (comps.get(r) ?? comps.set(r, []).get(r)).push(i); }
    for (const members of comps.values()) {
      const bb = box(members.map(i => tris[i]));
      const cxm = (bb.lo[0] + bb.hi[0]) / 2, czm = (bb.lo[2] + bb.hi[2]) / 2;
      const atEnd = czm > zMax - L * .12 || czm < zMin + L * .12;
      if (Math.max(...bb.size) < (cfg.badgeSize ?? .22) && Math.abs(cxm) < .14 && atEnd && bb.lo[1] > .25) {
        for (const i of members) tris[i].role = 'drop'; dropped += members.length;
      }
    }
  }
  // Emblems modelled into the grille or boot lid (CARLA): chrome/trim triangles in a small centreline box at the
  // nose become dark grille trim, at the tail they take the body paint, so no logo shape reads.
  if (cfg.emblems) {
        const boxes = [...(cfg.emblems.front ? [{ lo: [-.17, Hm * .15, zMax - .32], hi: [.17, Hm * .62, zMax + .1], to: cfg.emblems.front }] : []),
      { lo: [-.3, .45, zMin - .1], hi: [.3, 1.45, zMin + .3], to: cfg.emblems.rear ?? 'paint', only: ['chrome'] }, ...(cfg.emblems.extra ?? [])];
    // flat / flatPaint: the logo relief is pressed onto one plane facing forward and takes the box's median
    // colour (flatPaint: the body paint), so neither shading nor colour shows a logo shape.
    const inBox = (t, bx) => ctr(t).every((v, k) => v >= (bx.loRel ? [0, 0, zMax][k] + bx.lo[k] : bx.lo[k]) && v <= (bx.loRel ? [0, 0, zMax][k] + bx.hi[k] : bx.hi[k]));
    for (const bx of boxes.filter(b => b.to === 'flat' || b.to === 'flatPaint')) {
      const hit = tris.filter(t => !t.wheel && !['glass', 'plate', 'lampFront', 'lampTail', 'indicator', 'drop'].includes(t.role) && inBox(t, bx)
        && (!bx.key || new RegExp(bx.key, 'i').test(t.key)));
      if (!hit.length) continue;
      const zs = hit.flatMap(t => [t.p[2], t.p[5], t.p[8]]).sort((a, c) => a - c), plane = zs[Math.floor(zs.length * .3)];
      const med = [0, 1, 2].map(k => { const v = hit.flatMap(t => [t.c[k], t.c[k+3], t.c[k+6]]).sort((a, c) => a - c); return v[v.length >> 1]; });
      for (const t of hit) {
        for (let i = 0; i < 9; i += 3) { t.p[i+2] = Math.min(t.p[i+2], plane); t.n[i] = 0; t.n[i+1] = 0; t.n[i+2] = 1; }
        for (let i = 0; i < 9; i++) t.c[i] = med[i % 3];
        t.role = bx.to === 'flatPaint' ? (cfg.livery ? 'livery' : 'paint') : 'trim';
      }
      dropped += hit.length;
    }
    for (const bx of boxes) for (const t of tris) {
      if (bx.to === 'flat' || bx.to === 'flatPaint' || bx.key && !new RegExp(bx.key, 'i').test(t.key)) continue;
      if (t.wheel || !(bx.to === 'drop' ? !['glass', 'plate', 'lampFront', 'lampTail', 'indicator', ...(bx.keepPaint ? ['paint', 'livery'] : [])].includes(t.role)
        : (bx.only ?? ['chrome', 'trim', 'rim', 'interior']).includes(t.role))) continue;
      if (!ctr(t).every((v, k) => v >= (bx.loRel ? [0, 0, zMax][k] + bx.lo[k] : bx.lo[k]) && v <= (bx.loRel ? [0, 0, zMax][k] + bx.hi[k] : bx.hi[k]))) continue;
      if (bx.to === 'drop') t.role = 'drop';
      else if (bx.to === 'dark') { t.role = 'trim'; t.c.fill(.018); } else t.role = cfg.livery ? 'livery' : 'paint';
      dropped++;
    }
  }
  for (const d of cfg.drop ?? []) for (const t of tris) { const c = ctr(t); if (c.every((v, k) => v >= d.lo[k] && v <= d.hi[k]) && (!d.key || new RegExp(d.key, 'i').test(t.key))) { t.role = 'drop'; dropped++; } }
  // Painted lettering in a texture (truck cab names): flatten the colours inside the box to their median.
  for (const d of cfg.blank ?? []) {
    const inside = tris.filter(t => ctr(t).every((v, k) => v >= d.lo[k] && v <= d.hi[k]) && (!d.key || new RegExp(d.key, 'i').test(t.key)));
    const med = [0, 1, 2].map(k => { const v = inside.flatMap(t => [t.c[k], t.c[k+3], t.c[k+6]]).sort((a, c) => a - c); return v[v.length >> 1] ?? 0; });
    for (const t of inside) for (let i = 0; i < 9; i++) t.c[i] = med[i % 3];
  }
  console.log(JSON.stringify({ debadged: dropped }));
  for (let i = tris.length - 1; i >= 0; i--) if (tris[i].role === 'drop') tris.splice(i, 1);
}

// A skinned tyre's outer radius: hub height above its bone group's lowest point (the contact patch lies straight below
// the hub; mudguards and arches lie above it), with 3 % and 1 cm for the tread.
if (boneAxles) for (const h of Object.values(hubs)) {
  const own = boned.filter(t => h.bones.has(t.wheel) && Math.sign(ctr(t)[0]) === Math.sign(h.hub[0]));
  const low = own.reduce((m, t) => Math.min(m, t.p[1], t.p[4], t.p[7]), Infinity);
  h.tyre = (h.hub[1] - low) * 1.03 + .01;
}
const partOf = t => {
  // Bone-weighted wheel triangles go to the nearest hub of their own axle, but only inside its tyre: mudguards and
  // arch parts weighted to a wheel bone would spin round the tyre, and middle-axle bones stay in the body.
  if (t.wheel) { const c = ctr(t);
    const [name, , h] = Object.entries(hubs).filter(([, h]) => !h.bones || h.bones.has(t.wheel)).reduce((best, [name, h]) =>
      Math.hypot(c[0]-h.hub[0], c[1]-h.hub[1], c[2]-h.hub[2]) < best[1] ? [name, Math.hypot(c[0]-h.hub[0], c[1]-h.hub[1], c[2]-h.hub[2]), h] : best, ['body', Infinity, null]);
    if (!h) return 'body';
    const reach = h.tyre ?? Infinity;
    return [0, 3, 6].every(i => Math.hypot(t.p[i+1] - h.hub[1], t.p[i+2] - h.hub[2]) <= reach) ? name : 'body'; }
  if (t.role !== 'tyre' && t.role !== 'rim' && !(cfg.captureCylinder && t.role !== 'paint' && t.role !== 'glass')) return 'body';
  for (const [name, h] of Object.entries(hubs)) {
    const inside = [0, 3, 6].every(i => Math.abs(t.p[i] - h.hub[0]) <= width / 2 + .06
      && Math.hypot(t.p[i+1] - h.hub[1], t.p[i+2] - h.hub[2]) <= radius * (t.role === 'tyre' || t.role === 'rim' ? 1.06 : .97));
    if (inside) return name;
  }
  return 'body';
};
const parts = { body: [], interior: [], glass: [], wheel_FL: [], wheel_FR: [], wheel_RL: [], wheel_RR: [] };
for (const t of tris) {
  const part = t.role === 'interior' ? 'interior' : t.role === 'glass' ? 'glass' : partOf(t);
  parts[part].push(t);
}
for (const name of ['wheel_FL', 'wheel_FR', 'wheel_RL', 'wheel_RR']) if (parts[name].length < 20) throw Error(`${name} has ${parts[name].length} triangles`);

// ---- Posed wheels ------------------------------------------------------------------------------------------------
// Sources often pose the front wheels steered (e30, punto-1995 and generic-sedan stood at 17-26° toe). The runtime spins
// every hub about the model X axis and steers it about Y, so a wheel whose own axle is off X precesses like a tossed
// coin as it rolls, and a tyre whose centre is off its hub orbits it. Turn each wheel part about its tyre's centre until
// the tyre axle (the least-spread direction of the tyre surface) lies on X, then move that centre onto the hub (the
// symmetric layout above moved hubs by the left/right differences). The measured width then becomes the true tyre width
// instead of the steered wheel's footprint.
function axleOf(list, [hx, hy, hz]) {
  const cov = [0, 0, 0, 0, 0, 0, 0, 0, 0], sum = [0, 0, 0];
  let area = 0;
  for (const t of list) {
    const e1 = [0, 1, 2].map(k => t.p[3 + k] - t.p[k]), e2 = [0, 1, 2].map(k => t.p[6 + k] - t.p[k]);
    const w = Math.hypot(e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]) / 2;
    const c = ctr(t), v = [c[0] - hx, c[1] - hy, c[2] - hz];
    area += w; for (let r = 0; r < 3; r++) { sum[r] += w * v[r]; for (let k = 0; k < 3; k++) cov[r * 3 + k] += w * v[r] * v[k]; }
  }
  const m = sum.map(a => a / area);
  const a = [0, 1, 2].map(r => [0, 1, 2].map(k => cov[r * 3 + k] / area - m[r] * m[k])), v = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  // Jacobi sweeps on the symmetric 3x3 covariance; the axle is the eigenvector of the smallest eigenvalue.
  for (let sweep = 0; sweep < 50; sweep++) for (let p = 0; p < 2; p++) for (let q = p + 1; q < 3; q++) {
    if (Math.abs(a[p][q]) < 1e-15) continue;
    const th = (a[q][q] - a[p][p]) / (2 * a[p][q]), tt = Math.sign(th || 1) / (Math.abs(th) + Math.sqrt(th * th + 1));
    const cs = 1 / Math.sqrt(tt * tt + 1), sn = tt * cs;
    for (let k = 0; k < 3; k++) { const x = a[k][p], y = a[k][q]; a[k][p] = cs * x - sn * y; a[k][q] = sn * x + cs * y; }
    for (let k = 0; k < 3; k++) { const x = a[p][k], y = a[q][k]; a[p][k] = cs * x - sn * y; a[q][k] = sn * x + cs * y; }
    for (let k = 0; k < 3; k++) { const x = v[k][p], y = v[k][q]; v[k][p] = cs * x - sn * y; v[k][q] = sn * x + cs * y; }
  }
  const i = [0, 1, 2].sort((x, y) => a[x][x] - a[y][y])[0], axle = [v[0][i], v[1][i], v[2][i]], s = Math.sign(axle[0]) || 1;
  return axle.map(c => c * s / Math.hypot(...axle));
}
let unposed = false;
for (const name of ['wheel_FL', 'wheel_FR', 'wheel_RL', 'wheel_RR']) {
  const h = hubs[name], tyres = parts[name].filter(t => t.role === 'tyre');
  // Skinned wheels can carry parts outside the tyre (mudguards); only a real tyre set defines the centre.
  if (tyres.length <= 40) continue;
  const tb = box(tyres), centre = tb.lo.map((lo, k) => (lo + tb.hi[k]) / 2), axle = axleOf(tyres, centre);
  const angle = Math.acos(Math.min(1, axle[0])), turned = angle >= .5 * Math.PI / 180;
  const shift = [0, h.hub[1] - centre[1], h.hub[2] - centre[2]];
  if (!turned && Math.hypot(shift[1], shift[2]) < .002) continue;
  // Rodrigues rotation about axle × X takes the axle onto X; positions turn about the tyre centre, normals about the origin.
  const kn = Math.hypot(axle[2], axle[1]) || 1, k = [0, axle[2] / kn, -axle[1] / kn], c = turned ? Math.cos(angle) : 1, s = turned ? Math.sin(angle) : 0;
  const turn = (x, y, z) => { const d = k[0] * x + k[1] * y + k[2] * z;
    return [x * c + (k[1] * z - k[2] * y) * s + k[0] * d * (1 - c), y * c + (k[2] * x - k[0] * z) * s + k[1] * d * (1 - c), z * c + (k[0] * y - k[1] * x) * s + k[2] * d * (1 - c)]; };
  for (const t of parts[name]) for (let i = 0; i < 9; i += 3) {
    const p = turn(t.p[i] - centre[0], t.p[i + 1] - centre[1], t.p[i + 2] - centre[2]), n = turn(t.n[i], t.n[i + 1], t.n[i + 2]);
    t.p.splice(i, 3, ...p.map((v, j) => v + centre[j] + shift[j])); t.n.splice(i, 3, ...n);
  }
  // A steered pose widened the measured footprint; a small camber correction leaves the outboard measure as it was.
  if (angle > 5 * Math.PI / 180) { h.width = box(tyres).size[0]; unposed = true; }
  console.log(JSON.stringify({ wheel: name, turnedDegrees: +(angle * 180 / Math.PI).toFixed(2), centredCm: +(Math.hypot(shift[1], shift[2]) * 100).toFixed(2) }));
}
if (unposed) width = Object.values(hubs).reduce((s, h) => s + h.width, 0) / 4;

// ---- Simplification ----------------------------------------------------------------------------------------------
function indexed(list, origin = [0, 0, 0]) {
  const map = new Map(), pos = [], att = [], roles = [], idx = [];
  const q = a => Math.round(a * 1e4);
  for (const t of list) for (let k = 0; k < 3; k++) {
    const p = [t.p[k*3] - origin[0], t.p[k*3+1] - origin[1], t.p[k*3+2] - origin[2]], n = t.n.slice(k*3, k*3+3), c = t.c.slice(k*3, k*3+3);
    const role = t.role === 'paint' ? 1 : ROLE[t.role];
    if (t.role === 'paint') c.fill(1);
    const key = `${p.map(q)}|${n.map(a => Math.round(a * 100))}|${c.map(a => Math.round(a * 50))}|${role}`;
    let i = map.get(key);
    if (i === undefined) { i = pos.length / 3; map.set(key, i); pos.push(...p); att.push(...n, ...c); roles.push(role); }
    idx.push(i);
  }
  return { pos: new Float32Array(pos), att: new Float32Array(att), roles, idx: new Uint32Array(idx) };
}
function simplify(mesh, target, error, sloppy = false, prune = false) {
  // Prune drops disconnected shells; car bodies are built from separate panels, so only interiors use it.
  const flags = prune ? ['Prune'] : [];
  let idx = mesh.idx;
  if (idx.length / 3 > target) {
    [idx] = MeshoptSimplifier.simplifyWithAttributes(mesh.idx, mesh.pos, 3, mesh.att, 6, [.5, .5, .5, 1, 1, 1], null, target * 3, error, flags);
    if (idx.length / 3 > target * 1.05) [idx] = MeshoptSimplifier.simplifyWithAttributes(mesh.idx, mesh.pos, 3, mesh.att, 6, [.2, .2, .2, .4, .4, .4], null, target * 3, error * 4, [...flags, 'Permissive']);
    if (idx.length / 3 > target * 1.05) [idx] = MeshoptSimplifier.simplifyWithAttributes(mesh.idx, mesh.pos, 3, mesh.att, 6, [.05, .05, .05, .1, .1, .1], null, target * 3, error * 20, [...flags, 'Permissive']);
    if (sloppy && idx.length / 3 > target * 1.05) { console.log(JSON.stringify({ sloppy: idx.length / 3, target }));
      [idx] = MeshoptSimplifier.simplifySloppy(idx, mesh.pos, 3, null, target * 3, error * 4); }
  }
  return { ...mesh, idx };
}
const triCount = m => m.idx.length / 3;
const budgets = cfg.budgets ?? [32000, 8000, 3000];
const wheelShare = [.05, .05, .03], glassShare = [.05, .06, .08], interiorShare = [.08, 0, 0];

// Fleet material roles, as the shared node material reads them (actors/fleet/authored.ts flattenFleetAsset).
const MATERIALS = {
  1: ['fleet_paint', [1, 1, 1, 1], .22, .35], 2: ['fleet_trim', [1, 1, 1, 1], .6, 0], 3: ['fleet_chrome', [1, 1, 1, 1], .2, .85],
  4: ['fleet_glass', [1, 1, 1, .74], .06, .7], 5: ['fleet_rubber', [1, 1, 1, 1], .85, 0], 6: ['fleet_alloy', [1, 1, 1, 1], .28, .85],
  7: ['fleet_interior', [1, 1, 1, 1], .85, 0], 8: ['fleet_plate', [.80, .82, .78, 1], .3, .2],
  13: ['fleet_livery', [1, 1, 1, 1], .25, .3],
};
const PLATE_COLOUR = [1, 1, 1];
function writeLod(lod) {
  const out = new Document(), buffer = out.createBuffer(), scene = out.createScene('car');
  const mats = new Map();
  const material = (name, color, rough, metal, blend) => {
    if (!mats.has(name)) {
      const m = out.createMaterial(name).setBaseColorFactor(color).setRoughnessFactor(rough).setMetallicFactor(metal).setDoubleSided(true);
      if (blend) m.setAlphaMode('BLEND'); mats.set(name, m);
    }
    return mats.get(name);
  };
  const lampMaterial = (role, x) => role === ROLE.lampFront ? material(`lamp-front-${x > 0 ? 'left' : 'right'}`, [1, 1, 1, 1], .15, .6)
    : role === ROLE.lampTail ? material(`lamp-tail-${x > 0 ? 'left' : 'right'}`, [1, 1, 1, 1], .18, .3)
    : material(`fleet_indicator_${x > 0 ? 'left' : 'right'}`, [1, .55, .12, 1], .2, .2);
  let total = 0;
  const emit = (name, mesh, translation) => {
    const groups = new Map();
    for (let i = 0; i < mesh.idx.length; i += 3) {
      const v = [mesh.idx[i], mesh.idx[i+1], mesh.idx[i+2]], role = mesh.roles[v[0]];
      const x = (mesh.pos[v[0]*3] + mesh.pos[v[1]*3] + mesh.pos[v[2]*3]) / 3 + translation[0];
      const key = isLamp(role) ? `${role}/${x > 0 ? 'L' : 'R'}` : `${role}`;
      if (!groups.has(key)) groups.set(key, { role, x, idx: [] });
      groups.get(key).idx.push(...v);
    }
    const gltfMesh = out.createMesh(name);
    for (const { role, x, idx } of groups.values()) {
      const used = [...new Set(idx)], remap = new Map(used.map((v, i) => [v, i]));
      const P = new Float32Array(used.length * 3), N = new Float32Array(used.length * 3), C = new Float32Array(used.length * 3);
      used.forEach((v, i) => { for (let k = 0; k < 3; k++) {
        P[i*3+k] = mesh.pos[v*3+k]; N[i*3+k] = mesh.att[v*6+k]; C[i*3+k] = role === ROLE.plate ? PLATE_COLOUR[k] : mesh.att[v*6+3+k];
      } });
      const mat = isLamp(role) ? lampMaterial(role, x) : material(...MATERIALS[role], role === ROLE.glass);
      const prim = out.createPrimitive().setMaterial(mat)
        .setAttribute('POSITION', out.createAccessor().setType('VEC3').setArray(P).setBuffer(buffer))
        .setAttribute('NORMAL', out.createAccessor().setType('VEC3').setArray(N).setBuffer(buffer))
        .setAttribute('COLOR_0', out.createAccessor().setType('VEC3').setArray(C).setBuffer(buffer))
        .setIndices(out.createAccessor().setType('SCALAR').setArray(new Uint32Array(idx.map(v => remap.get(v)))).setBuffer(buffer));
      gltfMesh.addPrimitive(prim);
    }
    total += mesh.idx.length / 3;
    scene.addChild(out.createNode(name).setMesh(gltfMesh).setTranslation(translation));
  };
  const B = budgets[lod];
  const wheelTarget = Math.round(B * wheelShare[lod]);
  const glass = simplify(indexed(parts.glass), Math.round(B * glassShare[lod]), [.002, .01, .04][lod]);
  const interior = interiorShare[lod] && parts.interior.length ? simplify(indexed(parts.interior), Math.round(B * interiorShare[lod]), .02, true, true) : null;
  const wheels = Object.entries(hubs).map(([name, h]) => [name, h, simplify(indexed(parts[name], h.hub), wheelTarget, [.003, .02, .1][lod], lod === 2)]);
  const used = triCount(glass) + (interior ? triCount(interior) : 0) + wheels.reduce((s, [, , m]) => s + triCount(m), 0);
  const body = simplify(indexed(parts.body), Math.max(200, Math.floor((B - used) * .98)), [.0015, .006, .03][lod], lod === 2);
  emit('body', body, [0, 0, 0]);
  if (triCount(glass)) emit('glass', glass, [0, 0, 0]);
  if (interior && triCount(interior)) emit('interior', interior, [0, 0, 0]);
  for (const [name, h, m] of wheels) emit(name, m, h.hub);
  return { out, total };
}
const counts = [];
for (let lod = 0; lod < 3; lod++) {
  const { out, total } = writeLod(lod);
  if (total > budgets[lod] * 1.08) throw Error(`LOD${lod} over budget: ${total} > ${budgets[lod]}`);
  await io.write(path.join(work, `lod${lod}.glb`), out);
  counts.push(total);
  console.log(JSON.stringify({ id, lod, triangles: total }));
}

// ---- Registry entry ------------------------------------------------------------------------------------------------
const ext = box(tris, exterior), round = a => Math.round(a * 1e4) / 1e4;
const wheelbase = hubs.wheel_FL.hub[2] - hubs.wheel_RL.hub[2];
const fleet = { id, name: cfg.name, body: cfg.body, family: cfg.family, length: round(ext.size[2]), width: round(ext.size[0]),
  height: round(ext.size[1]), wheelbase: round(wheelbase), hood: .25, rear: -.4, taper: .82, wheel: round(radius),
  belt: 1, face: cfg.face ?? 0, accessory: 'none', age: cfg.age ?? .1, ...(cfg.weight !== undefined ? { weight: cfg.weight } : {}) };
const destination = path.join(repo, 'game/public/assets/cars', id);
execFileSync('node', [path.join(repo, 'game/scripts/prepare-fleet.mjs'), work, destination], { stdio: 'inherit' });
const recordPath = path.join(path.dirname(source), 'sketchfab-record.json');
const record = fs.existsSync(recordPath) ? JSON.parse(fs.readFileSync(recordPath, 'utf8')) : null;
const entry = {
  assetBase: `/assets/cars/${id}`, fleet, id,
  wheels: { radius: round(radius), width: round(width), pivots: Object.fromEntries(Object.entries(hubs).map(([n, h]) => [n, h.hub.map(round)])) },
  // Axis-aligned collision box around the exterior bodywork (centre, half extents), model space.
  collision: { center: [0, round(ext.size[1] / 2), round((ext.lo[2] + ext.hi[2]) / 2)], half: [round(ext.size[0] / 2), round(ext.size[1] / 2), round(ext.size[2] / 2)] },
  triangles: counts,
  source: cfg.credit ?? { title: record.name, url: record.viewerUrl, author: record.user.displayName, licence: record.license.label, licenceUrl: record.license.url },
  spec: { id, ...cfg.spec, lengthM: fleet.length, widthM: fleet.width, heightM: fleet.height, wheelbaseM: fleet.wheelbase,
    trackM: round(hubs.wheel_FR.hub[0] * 2), wheelRadiusM: round(radius), wheelWidthM: round(width) },
  accepted: false,
};
fs.writeFileSync(path.join(repo, 'game/src/actors/fleet/models', `${id}.json`), JSON.stringify(entry, null, 2) + '\n');
console.log(JSON.stringify({ hubs: Object.fromEntries(Object.entries(hubs).map(([n, h]) => [n, [...h.hub.map(round), round(h.radius), round(h.width)]])) }));
console.log(JSON.stringify({ id, triangles: counts, length: fleet.length, width: fleet.width, height: fleet.height, wheelbase: fleet.wheelbase, radius: round(radius), forward }));
