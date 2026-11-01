// Ambient walker bodies: CC0 MakeHuman outfits retargeted into the CC0 Quaternius UAL rig (scripts/lib/character-outfit.mjs),
// simplified with meshoptimizer and baked to one vertex animation texture (VAT) for UAL Walk_Loop and Idle_Loop.
// Usage (from game/): node scripts/bake-walkers.mjs <source-directory> <UAL.glb> <output-directory>
// Rebuild instructions: public/assets/walkers/README.md. Runtime decoder: src/world/walkers/asset.ts.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { MeshoptDecoder, MeshoptSimplifier } from 'meshoptimizer';
import { DataUtils, Matrix4, Quaternion, Vector3 } from 'three';
import { BODY_ZONES, CHARACTER_CREATOR_REVISION, MORPHS, addOutfit, loadRig } from './lib/character-outfit.mjs';

const [sourceDir, rigFile, outputDir] = process.argv.slice(2);
if (!sourceDir || !rigFile || !outputDir) throw new Error('usage: bake-walkers.mjs <source-dir> <UAL.glb> <output-dir>');

/** Body regions; the runtime tints each one per instance (src/world/walkers/asset.ts REGION). */
const REGION = { skin: 0, top: 1, bottom: 2, shoes: 3, hair: 4, inner: 5, legwear: 6 };
const MATERIAL_REGION = {
  Skin: REGION.skin, Cloth_hoodie: REGION.top, Cloth_jacket: REGION.top, Cloth_shirt: REGION.inner,
  Cloth_jeans: REGION.bottom, Cloth_skirt: REGION.bottom, Cloth_shoes: REGION.shoes, Cloth_boots: REGION.shoes,
};
const region = name => name.startsWith('Hair_') ? REGION.hair : MATERIAL_REGION[name];
/** Eyes, teeth, tongue and brows are a few pixels at walker distances; they are dropped, not tinted. */
const DROPPED = new Set(['Eyebrow', 'Eyelash', 'Eye', 'Iris', 'Cornea', 'Teeth', 'Tongue']);

const VARIANTS = [
  { id: 'hoodie', parts: ['base_body', 'clothing_hoodie', 'clothing_jeans', 'clothing_shoes', 'hair_short02'],
    hidden: BODY_ZONES.feet | BODY_ZONES.legs | BODY_ZONES.hoodie, morphs: [[MORPHS.male, 0.65]] },
  { id: 'jacket', parts: ['base_body', 'clothing_shirt', 'clothing_jacket', 'clothing_jeans', 'clothing_boots', 'hair_short04'],
    hidden: BODY_ZONES.shirt | BODY_ZONES.jacket | BODY_ZONES.legs | BODY_ZONES.boots, morphs: [[MORPHS.male, 0.9]] },
  // Bare skin below the skirt becomes its own region (tights or skin, chosen per instance).
  { id: 'skirt', parts: ['base_body', 'clothing_shirt', 'clothing_jacket', 'clothing_skirt', 'clothing_boots', 'hair_ponytail01'],
    hidden: BODY_ZONES.shirt | BODY_ZONES.jacket | BODY_ZONES.hips | BODY_ZONES.boots, morphs: [[MORPHS.female, 0.75]], legwearBelow: 0.8 },
];
const CLIPS = [{ id: 'walk', name: 'Walk_Loop' }, { id: 'idle', name: 'Idle_Loop' }];
const FPS = 30;
/** A sole vertex this close to y = 0 (the rig's ground) touches the ground. */
const SOLE_CONTACT = 0.02;
/** Texture width: every variant keeps at most this many vertices, so the VAT stays under 2 MB. */
const MAX_VERTICES = 720;
/** Spherical Fibonacci normal directions; indices up to 2048 are exact in half floats. */
const NORMAL_DIRECTIONS = 2048;
const GOLDEN_FRACTION = Math.fround(0.3819660112501051); // 2 - golden ratio; the shader uses the same f32 constant

await Promise.all([MeshoptDecoder.ready, MeshoptSimplifier.ready]);
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.decoder': MeshoptDecoder });

const directions = new Float32Array(NORMAL_DIRECTIONS * 3);
for (let i = 0; i < NORMAL_DIRECTIONS; i++) {
  const z = Math.fround(1 - (2 * i + 1) / NORMAL_DIRECTIONS), r = Math.sqrt(Math.max(0, 1 - z * z));
  const phi = 2 * Math.PI * (Math.fround(i * GOLDEN_FRACTION) % 1);
  directions.set([Math.cos(phi) * r, Math.sin(phi) * r, z], 3 * i);
}
/** Nearest direction index; directions are ordered by z, so only a band of them is searched. */
function encodeNormal(x, y, z) {
  const centre = (1 - z) * NORMAL_DIRECTIONS / 2, band = 0.09 * NORMAL_DIRECTIONS / 2;
  let best = 0, bestDot = -2;
  for (let i = Math.max(0, Math.floor(centre - band)); i <= Math.min(NORMAL_DIRECTIONS - 1, Math.ceil(centre + band)); i++) {
    const d = directions[3 * i] * x + directions[3 * i + 1] * y + directions[3 * i + 2] * z;
    if (d > bestDot) { bestDot = d; best = i; }
  }
  return best;
}

/** One merged, welded, simplified body: bind positions/normals, joints/weights and a region per vertex. */
function collectBody(doc, legwearBelow = 0) {
  const position = [], normal = [], joints = [], weights = [], regions = [], indices = [];
  const weld = new Map();
  for (const node of doc.getRoot().listNodes()) {
    const mesh = node.getMesh();
    if (!mesh) continue;
    for (const primitive of mesh.listPrimitives()) {
      const name = primitive.getMaterial()?.getName() ?? '';
      if (DROPPED.has(name)) continue;
      const material = region(name);
      if (material === undefined) throw new Error(`walker bake: no region for material ${name}`);
      const pos = primitive.getAttribute('POSITION'), norm = primitive.getAttribute('NORMAL');
      const ids = primitive.getAttribute('JOINTS_0'), w = primitive.getAttribute('WEIGHTS_0');
      const remap = new Uint32Array(pos.getCount());
      for (let i = 0; i < pos.getCount(); i++) {
        const p = pos.getElement(i, []);
        const r = material === REGION.skin && p[1] < legwearBelow ? REGION.legwear : material;
        // UV seams duplicate vertices; without UVs they are one vertex of their region.
        const key = `${r}:${p.map(v => Math.round(v * 1e5)).join(',')}`;
        let k = weld.get(key);
        if (k === undefined) {
          k = regions.length; weld.set(key, k);
          position.push(...p); normal.push(...norm.getElement(i, [])); joints.push(...ids.getElement(i, []));
          weights.push(...w.getElement(i, [])); regions.push(r);
        }
        remap[i] = k;
      }
      for (const i of primitive.getIndices().getArray()) indices.push(remap[i]);
    }
  }
  return { position: new Float32Array(position), normal: new Float32Array(normal), joints: new Uint16Array(joints),
    weights: new Float32Array(weights), regions: new Uint8Array(regions), indices: new Uint32Array(indices) };
}

/** Simplify to the largest triangle count whose compacted mesh fits MAX_VERTICES. */
function simplifyBody(body) {
  const attempt = target => {
    const [simplified] = MeshoptSimplifier.simplify(body.indices, body.position, 3, target, 1);
    const order = [], seen = new Map();
    const indices = simplified.map(v => { let k = seen.get(v); if (k === undefined) { k = order.length; seen.set(v, k); order.push(v); } return k; });
    return { order, indices: Uint32Array.from(indices) };
  };
  let low = 300, high = body.indices.length / 3, best = attempt(low * 3);
  if (best.order.length > MAX_VERTICES) throw new Error('walker bake: cannot reach the vertex budget');
  while (high - low > 8) {
    const mid = Math.floor((low + high) / 2), next = attempt(mid * 3);
    if (next.order.length <= MAX_VERTICES) { best = next; low = mid; } else high = mid;
  }
  return best;
}

/** Linear keyframe sampling of the clip's node TRS channels at time t. */
function poseAt(clip, t) {
  for (const channel of clip.listChannels()) {
    const sampler = channel.getSampler(), times = sampler.getInput().getArray(), out = sampler.getOutput();
    const node = channel.getTargetNode(), pathName = channel.getTargetPath();
    if (!node || !['translation', 'rotation', 'scale'].includes(pathName)) continue;
    if (sampler.getInterpolation() === 'CUBICSPLINE') throw new Error('walker bake: cubic spline clips are not supported');
    let k = 0;
    while (k < times.length - 2 && times[k + 1] <= t) k++;
    const span = times[k + 1] - times[k], u = sampler.getInterpolation() === 'STEP' || !(span > 0) ? 0 : Math.min(1, Math.max(0, (t - times[k]) / span));
    const a = out.getElement(k, []), b = out.getElement(Math.min(k + 1, times.length - 1), []);
    if (pathName === 'rotation') {
      const q = new Quaternion().fromArray(a).slerp(new Quaternion().fromArray(b), u).normalize();
      node.setRotation(q.toArray());
    } else {
      const v = a.map((x, i) => x + (b[i] - x) * u);
      if (pathName === 'translation') node.setTranslation(v); else node.setScale(v);
    }
  }
}

/** Skinned positions and normals of the kept vertices for every frame of `clip`. */
function bakeClip(doc, body, keep, clip) {
  const root = doc.getRoot(), skin = root.listSkins()[0], jointNodes = skin.listJoints();
  const ibm = skin.getInverseBindMatrices();
  const inverseBind = jointNodes.map((_, j) => new Matrix4().fromArray(ibm.getElement(j, [])));
  const duration = Math.max(...clip.listSamplers().map(s => { const t = s.getInput().getArray(); return t[t.length - 1]; }));
  const frames = Math.round(duration * FPS);
  if (Math.abs(frames / FPS - duration) > 1e-3) throw new Error(`walker bake: ${clip.getName()} is not a whole number of ${FPS} fps frames`);
  const result = [], matrices = jointNodes.map(() => new Matrix4()), p = new Vector3(), n = new Vector3(), acc = new Vector3(), accN = new Vector3();
  for (let f = 0; f < frames; f++) {
    poseAt(clip, f / FPS);
    jointNodes.forEach((joint, j) => matrices[j].fromArray(joint.getWorldMatrix()).multiply(inverseBind[j]));
    const frame = new Float32Array(keep.length * 6);
    keep.forEach((v, i) => {
      acc.set(0, 0, 0); accN.set(0, 0, 0);
      for (let k = 0; k < 4; k++) {
        const w = body.weights[4 * v + k];
        if (!w) continue;
        const m = matrices[body.joints[4 * v + k]];
        acc.addScaledVector(p.fromArray(body.position, 3 * v).applyMatrix4(m), w);
        accN.addScaledVector(n.fromArray(body.normal, 3 * v).transformDirection(m), w);
      }
      accN.normalize();
      frame.set([acc.x, acc.y, acc.z, accN.x, accN.y, accN.z], 6 * i);
    });
    result.push(frame);
  }
  return { frames: result };
}

const variants = [], rows = [], provenance = new Map();
let width = 0;
for (const spec of VARIANTS) {
  const doc = await loadRig(io, rigFile, new Set(CLIPS.map(c => c.name)));
  const rest = new Map(doc.getRoot().listNodes().map(node => [node, [node.getTranslation(), node.getRotation(), node.getScale()]]));
  const { sources } = await addOutfit(io, doc, { sourceDir, parts: spec.parts, hidden: spec.hidden, morphs: spec.morphs });
  for (const s of sources) provenance.set(s.file, s.sha256);
  const body = collectBody(doc, spec.legwearBelow);
  const { order, indices } = simplifyBody(body);
  width = Math.max(width, order.length);
  const clips = [], rowBase = rows.length;
  let metresPerLoop = 0;
  for (const c of CLIPS) {
    const clip = doc.getRoot().listAnimations().find(a => a.getName() === c.name);
    if (!clip) throw new Error(`walker bake: rig has no ${c.name}`);
    for (const [node, [t, r, s]] of rest) node.setTranslation(t).setRotation(r).setScale(s);
    const baked = bakeClip(doc, body, order, clip);
    if (c.id === 'walk') {
      // The in-place loop slides each planted sole backwards at the ground speed it was authored for: the median
      // speed of shoe vertices touching the ground (the ankle joint understates it while the foot rolls).
      const shoes = order.flatMap((v, i) => body.regions[v] === REGION.shoes ? [i] : []), frames = baked.frames, speeds = [];
      frames.forEach((a, f) => {
        const b = frames[(f + 1) % frames.length];
        for (const i of shoes) if (a[6 * i + 1] < SOLE_CONTACT && b[6 * i + 1] < SOLE_CONTACT) speeds.push((a[6 * i + 2] - b[6 * i + 2]) * FPS);
      });
      if (!speeds.length) throw new Error('walker bake: no stance frames in the walk clip');
      speeds.sort((x, y) => x - y);
      metresPerLoop = speeds[speeds.length >> 1] * frames.length / FPS;
    }
    clips.push({ id: c.id, clip: c.name, row: rows.length - rowBase, frames: baked.frames.length, fps: FPS });
    rows.push(...baked.frames);
  }
  const first = rows[rowBase];
  let height = 0;
  for (let i = 0; i < order.length; i++) height = Math.max(height, first[6 * i + 1]);
  const regionCounts = Object.fromEntries(Object.entries(REGION).map(([name, r]) => [name, order.filter(v => body.regions[v] === r).length]));
  variants.push({ id: spec.id, parts: spec.parts, morphs: spec.morphs, vertices: order.length, triangles: indices.length / 3,
    sourceTriangles: body.indices.length / 3, rowBase, clips, height: Number(height.toFixed(3)), metresPerLoop: Number(metresPerLoop.toFixed(3)),
    regions: regionCounts, indices, vertexRegions: Uint8Array.from(order, v => body.regions[v]) });
}

// RGBA16F: xyz = model-space position (metres, +Z forward, feet at y = 0), w = normal direction index.
const vat = new Uint16Array(width * rows.length * 4);
rows.forEach((frame, row) => {
  for (let i = 0; i < frame.length / 6; i++) {
    const o = 4 * (row * width + i);
    vat[o] = DataUtils.toHalfFloat(frame[6 * i]); vat[o + 1] = DataUtils.toHalfFloat(frame[6 * i + 1]);
    vat[o + 2] = DataUtils.toHalfFloat(frame[6 * i + 2]);
    vat[o + 3] = DataUtils.toHalfFloat(encodeNormal(frame[6 * i + 3], frame[6 * i + 4], frame[6 * i + 5]));
  }
});

// walkers.bin: "TWLK", u32 version, u32 JSON byte length, JSON (space padded to 4 bytes), then the sections it lists.
const sections = [];
let offset = 0;
const add = (array) => { const at = offset; sections.push(array); offset += array.byteLength; offset += (4 - offset % 4) % 4; return { offset: at, length: array.length }; };
const meta = {
  format: 'turku3d-walkers', version: 1, fps: FPS,
  vat: { width, height: rows.length, format: 'rgba16float', normal: { encoding: 'spherical-fibonacci', directions: NORMAL_DIRECTIONS }, ...add(vat) },
  regions: Object.keys(REGION),
  variants: variants.map(v => ({ id: v.id, vertices: v.vertices, triangles: v.triangles, sourceTriangles: v.sourceTriangles,
    rowBase: v.rowBase, clips: v.clips, height: v.height, metresPerLoop: v.metresPerLoop, regionVertices: v.regions,
    indices: add(Uint16Array.from(v.indices)), regionsOffset: add(v.vertexRegions) })),
};
let json = Buffer.from(JSON.stringify(meta));
json = Buffer.concat([json, Buffer.alloc((4 - json.length % 4) % 4, 0x20)]);
const header = new Uint32Array([0x4b4c5754, 1, json.length]);
const body = Buffer.alloc(offset);
let at = 0;
for (const s of sections) { Buffer.from(s.buffer, s.byteOffset, s.byteLength).copy(body, at); at += s.byteLength; at += (4 - at % 4) % 4; }
fs.mkdirSync(outputDir, { recursive: true });
const file = path.join(outputDir, 'walkers.bin');
fs.writeFileSync(file, Buffer.concat([Buffer.from(header.buffer), json, body]));
const sha256 = createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const report = {
  bytes: fs.statSync(file).size, vatBytes: vat.byteLength, vat: { width, height: rows.length, format: 'rgba16float' }, sha256,
  variants: meta.variants.map(({ id, vertices, triangles, sourceTriangles, height, metresPerLoop, clips }) =>
    ({ id, vertices, triangles, sourceTriangles, height, metresPerLoop, clips: clips.map(c => `${c.clip} ${c.frames}f@${c.fps}`) })),
  sourceRevision: CHARACTER_CREATOR_REVISION,
  rig: { file: path.basename(rigFile), sha256: createHash('sha256').update(fs.readFileSync(rigFile)).digest('hex') },
  sources: [...provenance].sort().map(([f, h]) => ({ file: f, sha256: h })),
};
fs.writeFileSync(path.join(outputDir, 'PROVENANCE.json'), `${JSON.stringify(report, null, 2)}\n`);
console.log(JSON.stringify(report, null, 2));
