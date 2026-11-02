// Ambient walker bodies: CC0 MakeHuman outfits retargeted into the CC0 Quaternius UAL rig (scripts/lib/character-outfit.mjs),
// simplified into two LODs with meshoptimizer and baked to one vertex animation texture (VAT) for UAL Walk_Loop and Idle_Loop.
// Usage (from game/): node scripts/bake-walkers.mjs <source-directory> <UAL.glb> <output-directory>
// Rebuild instructions: public/assets/walkers/README.md. Runtime decoder: src/world/walkers/asset.ts.
import fs from 'node:fs';
import { civilianPose } from './lib/civilian-pose.mjs';
import { addWalkerAccessories } from './lib/walker-accessories.mjs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { MeshoptDecoder, MeshoptSimplifier } from 'meshoptimizer';
import { DataUtils, Matrix4, Quaternion, Vector3 } from 'three';
import { BODY_ZONES, CHARACTER_CREATOR_REVISION, MORPHS, addOutfit, loadRig } from './lib/character-outfit.mjs';

const [sourceDir, rigFile, outputDir] = process.argv.slice(2);
if (!sourceDir || !rigFile || !outputDir) throw new Error('usage: bake-walkers.mjs <source-dir> <UAL.glb> <output-dir>');

/** Body regions; the runtime tints each one per instance (src/world/walkers/asset.ts REGION). */
const REGION = { skin: 0, top: 1, bottom: 2, shoes: 3, hair: 4, inner: 5, legwear: 6, eyes: 7, iris: 8 };
const MATERIAL_REGION = {
  Skin: REGION.skin, Cloth_hoodie: REGION.top, Cloth_jacket: REGION.top, Cloth_shirt: REGION.inner,
  Accessory: REGION.shoes, AccessoryTrim: REGION.inner, Cloth_tshirt: REGION.top, Cloth_shorts: REGION.bottom, Cloth_dress: REGION.top, Eye: REGION.eyes, Iris: REGION.iris,
  Cloth_jeans: REGION.bottom, Cloth_skirt: REGION.bottom, Cloth_shoes: REGION.shoes, Cloth_boots: REGION.shoes,
};
const region = name => name.startsWith('Hair_') ? REGION.hair : MATERIAL_REGION[name];
/** Retain eyeballs and irises; remove translucent lashes/cornea and hidden mouth interiors. */
const DROPPED = new Set(['Eyebrow', 'Eyelash', 'Cornea', 'Teeth', 'Tongue']);

const wardrobe = JSON.parse(fs.readFileSync(new URL('../src/world/walkers/wardrobe.json', import.meta.url)));
const VARIANTS = wardrobe.families.map(f => ({ id: f.id, accessory: f.accessory, parts: ['base_body', ...f.parts],
  hidden: f.hidden.reduce((mask, zone) => mask | BODY_ZONES[zone], 0), morphs: [[MORPHS[f.sex], f.weight], ...(f.faceMorphs ?? [])] }));
const CLIPS = [{ id: 'walk', name: 'Walk_Loop' }, { id: 'idle', name: 'Idle_Loop' }];
const FPS = 30;
/** A sole vertex this close to y = 0 (the rig's ground) touches the ground. */
const SOLE_CONTACT = 0.02;
/** Texture width: every near mesh keeps at most this many vertices; all 96 recipes share twelve VAT families. */
const MAX_VERTICES = 2800;
const FAR_TRIANGLES = 650;
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
  const position = [], normal = [], joints = [], weights = [], regions = [], indices = [], uvs = [];
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
      const texcoord = primitive.getAttribute('TEXCOORD_0');
      const tile = atlasTile(primitive.getMaterial());
      const ids = primitive.getAttribute('JOINTS_0'), w = primitive.getAttribute('WEIGHTS_0');
      const remap = new Uint32Array(pos.getCount());
      for (let i = 0; i < pos.getCount(); i++) {
        const p = pos.getElement(i, []);
        const r = material === REGION.skin && p[1] < legwearBelow ? REGION.legwear : material;
        // UV seams duplicate vertices; without UVs they are one vertex of their region.
        const uv = texcoord ? texcoord.getElement(i, []) : [0.5, 0.5];
        const key = `${r}:${tile}:${uv.map(v => Math.round(v * 1e5)).join(',')}:${p.map(v => Math.round(v * 1e5)).join(',')}`;
        let k = weld.get(key);
        if (k === undefined) {
          k = regions.length; weld.set(key, k);
          position.push(...p); normal.push(...norm.getElement(i, [])); joints.push(...ids.getElement(i, []));
          weights.push(...w.getElement(i, [])); regions.push(r);
          // The atlas has a four-pixel extruded gutter; keep bilinear/mipmap sampling inside its cell.
          uvs.push(((tile % ATLAS_COLUMNS) + (4 + uv[0] * 504) / 512) / ATLAS_COLUMNS,
            (Math.floor(tile / ATLAS_COLUMNS) + (4 + uv[1] * 504) / 512) / ATLAS_ROWS);
        }
        remap[i] = k;
      }
      for (const i of primitive.getIndices().getArray()) indices.push(remap[i]);
    }
  }
  return { position: new Float32Array(position), normal: new Float32Array(normal), joints: new Uint16Array(joints),
    weights: new Float32Array(weights), regions: new Uint8Array(regions), uvs: new Float32Array(uvs), indices: new Uint32Array(indices) };
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
  // UAL's tightly clenched fingers over-fold the retargeted MakeHuman hands. Keep the shared arm/gait tracks,
  // but use a relaxed 30% finger curl for civilian walking and standing.
  const fingers = jointNodes.filter(n => /^DEF-(f_|thumb)/.test(n.getName()))
    .map(node => ({ node, rest: new Quaternion().fromArray(node.getRotation()) }));
  const civilian = civilianPose(jointNodes);
  const ibm = skin.getInverseBindMatrices();
  const inverseBind = jointNodes.map((_, j) => new Matrix4().fromArray(ibm.getElement(j, [])));
  const duration = Math.max(...clip.listSamplers().map(s => { const t = s.getInput().getArray(); return t[t.length - 1]; }));
  const frames = Math.round(duration * FPS);
  if (Math.abs(frames / FPS - duration) > 1e-3) throw new Error(`walker bake: ${clip.getName()} is not a whole number of ${FPS} fps frames`);
  const result = [], matrices = jointNodes.map(() => new Matrix4()), p = new Vector3(), n = new Vector3(), acc = new Vector3(), accN = new Vector3();
  for (let f = 0; f < frames; f++) {
    civilian.restore();
    poseAt(clip, f / FPS);
    for (const { node, rest } of fingers) node.setRotation(rest.clone().slerp(new Quaternion().fromArray(node.getRotation()), 0.30).toArray());
    civilian.apply();
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

const ATLAS_COLUMNS = 5, ATLAS_ROWS = 4;
const atlasEntries = [];
const sourceSurfaces = new Map();
fs.mkdirSync(outputDir, { recursive: true });
const atlasDir = path.join(sourceDir, 'atlas');
fs.mkdirSync(atlasDir, { recursive: true });
/** Extra tint masks are source metadata rather than glTF material references; copyToDocument cannot retain them. */
function sourceSurface(name) {
  const part = name.startsWith('Cloth_') ? `clothing_${name.slice(6)}` : name.startsWith('Hair_') ? `hair_${name.slice(5)}` : 'base_body';
  if (!sourceSurfaces.has(part)) {
    const file = path.join(sourceDir, `${part}.glb`);
    if (!fs.existsSync(file)) return {};
    const raw = fs.readFileSync(file), size = raw.readUInt32LE(12), json = JSON.parse(raw.toString('utf8', 20, 20 + size));
    const bin = 20 + size + 8, surfaces = {};
    for (const mat of json.materials ?? []) {
      const index = mat.extras?.ccMask?.index;
      let mask = null;
      if (index !== undefined) {
        const image = json.images[json.textures[index].source], view = json.bufferViews[image.bufferView];
        mask = path.join(atlasDir, `${part}-trim.${image.mimeType === 'image/jpeg' ? 'jpg' : 'png'}`);
        fs.writeFileSync(mask, raw.subarray(bin + (view.byteOffset ?? 0), bin + (view.byteOffset ?? 0) + view.byteLength));
      }
      surfaces[mat.name] = { mask };
    }
    sourceSurfaces.set(part, surfaces);
  }
  return sourceSurfaces.get(part)[name] ?? {};
}
function atlasTile(material) {
  const tex = material.getBaseColorTexture();
  const key = tex ? createHash('sha256').update(tex.getImage()).digest('hex') : 'white';
  let tile = atlasEntries.findIndex(e => e.key === key);
  if (tile >= 0) return tile;
  tile = atlasEntries.length;
  if (tile >= ATLAS_COLUMNS * ATLAS_ROWS) throw new Error('walker atlas capacity exceeded');
  const file = path.join(atlasDir, `${tile}.${tex?.getMimeType() === 'image/jpeg' ? 'jpg' : 'png'}`);
  if (tex) fs.writeFileSync(file, tex.getImage());
  atlasEntries.push({ key, file: tex ? file : null, name: material.getName(), tile, ...sourceSurface(material.getName()) });
  return tile;
}
const variants = [], rows = [], provenance = new Map();
let width = 0;
for (const spec of VARIANTS) {
  const doc = await loadRig(io, rigFile, new Set(CLIPS.map(c => c.name)));
  const rest = new Map(doc.getRoot().listNodes().map(node => [node, [node.getTranslation(), node.getRotation(), node.getScale()]]));
  const { sources } = await addOutfit(io, doc, { sourceDir, parts: spec.parts, hidden: spec.hidden, morphs: spec.morphs });
  for (const s of sources) provenance.set(s.file, s.sha256);
  addWalkerAccessories(doc, spec);
  const body = collectBody(doc, spec.legwearBelow);
  const { order, indices } = simplifyBody(body);
  const compactPositions = Float32Array.from(order.flatMap(v => Array.from(body.position.subarray(v * 3, v * 3 + 3))));
  const [farIndices] = MeshoptSimplifier.simplify(indices, compactPositions, 3, FAR_TRIANGLES * 3, 1);
  width = Math.max(width, order.length);
  const clips = [], rowBase = rows.length;
  let metresPerLoop = 0;
  for (const c of CLIPS) {
    const clip = doc.getRoot().listAnimations().find(a => a.getName() === c.name);
    if (!clip) throw new Error(`walker bake: rig has no ${c.name}`);
    for (const [node, [t, r, s]] of rest) node.setTranslation(t).setRotation(r).setScale(s);
    const baked = bakeClip(doc, body, order, clip);
    if (c.id === 'walk') {
      // Measure the rendered (half-float, interpolated) stance, not only authored frame boundaries.
      // Shoe roll gives several velocity clusters; choose the narrowest relative interval containing half
      // the planted samples. The ordinary median can sit between heel/toe clusters and make both slide.
      const shoes = order.flatMap((v, i) => body.regions[v] === REGION.shoes ? [i] : []), frames = baked.frames;
      const half = value => DataUtils.fromHalfFloat(DataUtils.toHalfFloat(value));
      const at = (i, phase) => {
        const f = phase * frames.length, a = Math.floor(f) % frames.length, b = (a + 1) % frames.length, t = f - Math.floor(f);
        return [1, 2].map(k => half(frames[a][6 * i + k]) * (1 - t) + half(frames[b][6 * i + k]) * t);
      };
      const samples = 240, strides = [];
      for (const i of shoes) for (let k = 0; k < samples; k++) {
        const a = at(i, k / samples), b = at(i, ((k + 1) / samples) % 1);
        if (a[0] < SOLE_CONTACT && b[0] < SOLE_CONTACT) strides.push((a[1] - b[1]) * samples);
      }
      if (!strides.length) throw new Error('walker bake: no stance frames in the walk clip');
      strides.sort((a, b) => a - b);
      const span = Math.floor(strides.length / 2);
      let best = Infinity;
      for (let i = 0; i + span < strides.length; i++) {
        const lo = strides[i], hi = strides[i + span], mid = (lo + hi) / 2;
        if (mid <= 0) continue;
        const error = (hi - lo) / (hi + lo);
        if (error < best) { best = error; metresPerLoop = mid; }
      }
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
    regions: regionCounts, indices, farIndices, uv: Float32Array.from(order.flatMap(v => Array.from(body.uvs.subarray(v * 2, v * 2 + 2)))), vertexRegions: Uint8Array.from(order, v => body.regions[v]) });
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
  format: 'turku3d-walkers', version: 3, fps: FPS, atlas: 'wardrobe.png', atlasFormat: 'albedo-trim',
  vat: { width, height: rows.length, format: 'rgba16float', normal: { encoding: 'spherical-fibonacci', directions: NORMAL_DIRECTIONS }, ...add(vat) },
  regions: Object.keys(REGION),
  variants: variants.map(v => ({ id: v.id, vertices: v.vertices, triangles: v.triangles, sourceTriangles: v.sourceTriangles,
    rowBase: v.rowBase, clips: v.clips, height: v.height, metresPerLoop: v.metresPerLoop, regionVertices: v.regions,
    indices: add(Uint16Array.from(v.indices)), farIndices: add(Uint16Array.from(v.farIndices)), uv: add(v.uv), regionsOffset: add(v.vertexRegions) })),
};
let json = Buffer.from(JSON.stringify(meta));
json = Buffer.concat([json, Buffer.alloc((4 - json.length % 4) % 4, 0x20)]);
const header = new Uint32Array([0x4b4c5754, 3, json.length]);
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
fs.writeFileSync(path.join(atlasDir, 'atlas.json'), JSON.stringify({ columns: ATLAS_COLUMNS, rows: ATLAS_ROWS, entries: atlasEntries }));
execFileSync('python3', [new URL('./lib/walker-atlas.py', import.meta.url).pathname, path.join(atlasDir, 'atlas.json'), path.join(outputDir, 'wardrobe.png')]);
report.atlas = { file: 'wardrobe.png', sha256: createHash('sha256').update(fs.readFileSync(path.join(outputDir, 'wardrobe.png'))).digest('hex'), sources: atlasEntries.map(({key,name})=>({sha256:key,material:name})) };
fs.writeFileSync(path.join(outputDir, 'PROVENANCE.json'), `${JSON.stringify(report, null, 2)}\n`);
console.log(JSON.stringify(report, null, 2));
