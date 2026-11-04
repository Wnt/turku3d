// Mean colours of each HD building, for its block LOD shell (shell-look migration, pipeline/turku3d/migrations/shell_look.py).
//
//   node shell_colours.mjs SPEC.json   ->  stdout JSON {buildings: {<placement id>: {roof, wall, grid, box}}}
//
// SPEC: {dir: the tile's product directory, placements: [{id, model, position}], models: {<id>: uri relative to dir}}.
// Per placement it reads the HD model (its KTX2/PNG/JPEG textures included) and returns, in linear RGB:
//   roof: area-weighted mean of its roof surfaces' textures x base colour factor (orthophoto, metal, landmark roofs);
//   wall: area-weighted mean of its textured walls (landmark and atlas facades), or for facade-kit walls the
//         program's wall colour with its trim share mixed in (the windows are the client's procedural glazing);
//   grid: [bay, storey, opening width fraction, opening height fraction] of its facade-kit program, or null;
//   box:  its footprint [minX, minZ, maxX, maxZ] in tile coordinates.
// Deterministic: textures decode through the repo's own Basis transcoder (game/public/basis), sampled at one
// fixed mip (the first at most 128 px wide) at each triangle's centroid UV.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const { NodeIO } = require('@gltf-transform/core');
const { ALL_EXTENSIONS } = require('@gltf-transform/extensions');
const { MeshoptDecoder } = await import(require.resolve('meshoptimizer'));
await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.decoder': MeshoptDecoder });
const basisDir = path.resolve(here, '../../../game/public/basis');
const BASIS = new Function('require', '__dirname', fs.readFileSync(path.join(basisDir, 'basis_transcoder.js'), 'utf8') + '\nreturn BASIS;')(
  require, basisDir);
const basis = await BASIS({ wasmBinary: fs.readFileSync(path.join(basisDir, 'basis_transcoder.wasm')) });
basis.initializeBasis();
let sharp = null;
try { sharp = require('sharp'); } catch { /* PNG/JPEG textures are rare on building models; they then count as unknown */ }

const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const linear = c => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
const hex = h => [1, 3, 5].map(i => linear(parseInt(h.slice(i, i + 2), 16)));

/** RGBA8 (sRGB) pixels of one mip: the first level at most 128 px wide. */
async function decode(image) {
  const bytes = image.getImage(), mime = image.getMimeType();
  if (!bytes || bytes.length < 8) return null;
  if (mime === 'image/ktx2' || String.fromCharCode(...bytes.subarray(1, 4)) === 'KTX') {
    const file = new basis.KTX2File(new Uint8Array(bytes));
    try {
      if (!file.isValid() || !file.startTranscoding()) return null;
      let level = 0;
      while (level < file.getLevels() - 1 && file.getImageLevelInfo(level, 0, 0).origWidth > 128) level++;
      const info = file.getImageLevelInfo(level, 0, 0);
      const out = new Uint8Array(file.getImageTranscodedSizeInBytes(level, 0, 0, 13 /* RGBA32 */));
      if (!file.transcodeImage(out, level, 0, 0, 13, 0, -1, -1)) return null;
      return { w: info.origWidth, h: info.origHeight, px: out };
    } finally { file.close(); file.delete(); }
  }
  if (!sharp) return null;
  const { data, info } = await sharp(Buffer.from(bytes)).resize({ width: 128, height: 128, fit: 'inside', kernel: 'nearest' })
    .ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  return { w: info.width, h: info.height, px: new Uint8Array(data) };
}

/** Facade-kit trim share outside the openings (game facadeProgram.ts programPigment): surrounds, courses, cornice,
 *  corner bands. Same rule the client used before this migration (render/sceneryMaterial.ts). */
function trimShare(p) {
  const bay = p.bay ?? 0, storey = p.storey ?? 0, [ow = 0, oh = 0, , w = 0] = p.opening ?? [];
  const [, cornice = 0, course = 0, corner = 0] = p.bands ?? [], [width = 0, height = 0] = p.size ?? [];
  if (!(bay > 0 && storey > 0)) return 0;
  const open = ow * bay * oh * storey, cell = bay * storey;
  const surround = w > 0 ? ((ow * bay + 2 * w) * (oh * storey + 2 * w) - open) / cell : 0;
  const share = surround + course / storey + (height > 0 ? cornice / height : 0) + (width > 0 ? 2 * corner / width : 0);
  return Math.min(.6, Math.max(0, share / Math.max(.2, 1 - open / cell)));
}
const programOf = extras => {
  const walls = extras.facade_kit_walls, kit = extras.facade_kit;
  return (Array.isArray(walls) ? walls : [kit]).find(p => p && typeof p === 'object' && typeof p.wall === 'string');
};

/** Mean linear colour of a facade-kit wall outside its openings, as game/src/render/facadeKit.ts paints it: the module
 *  atlas albedo of the program's upper-storey cell tinted by wall and trim (atlas alpha picks which), then the trim
 *  bands mixed in by their share. `atlas` is the kit material's decoded base colour (1024 x 2048 at level 0). */
function kitPigment(program, atlas) {
  const wall = hex(program.wall), trim = program.trim ? hex(program.trim) : wall;
  let rgb = wall;
  if (atlas && Number.isInteger(program.module)) {
    const cw = atlas.w / 8, ch = atlas.h / 16, x0 = (program.module % 7) * cw, y0 = Math.floor(program.module / 7) * 2 * ch;
    const sum = [0, 0, 0]; let n = 0;
    const g = Math.max(1, Math.round(cw * 4 / 128));
    for (let y = Math.floor(y0 + g); y < Math.floor(y0 + ch - g); y++) for (let x = Math.floor(x0 + g); x < Math.floor(x0 + cw - g); x++) {
      const o = (y * atlas.w + x) * 4, a = atlas.px[o + 3] / 255;
      const lo = Math.min(1, Math.max(0, 1 - 2 * a)), hi = Math.min(1, Math.max(0, 2 * a - 1));
      for (let k = 0; k < 3; k++) sum[k] += linear(atlas.px[o + k]) * (1 + (wall[k] - 1) * lo + (trim[k] - 1) * hi);
      n++;
    }
    if (n) rgb = sum.map(v => v / n);
  }
  const k = program.trim ? trimShare(program) : 0;
  return rgb.map((c, i) => c + (trim[i] - c) * k);
}

/** Surface ('roof' | 'wall' | null), kit program and kit pigment of one object's extras. */
function look(extras) {
  const key = String(extras.material_key ?? '');
  const surface = extras.surface === 'roof' || extras.surface === 'wall' ? extras.surface
    : key.startsWith('roof') ? 'roof' : key === 'facade_kit' || key.includes('wall') ? 'wall' : null;
  const program = surface === 'wall' && key === 'facade_kit' ? programOf(extras) : null;
  return { surface, program, kit: null };
}

const decoded = new Map();
const models = new Map();
/** Per model: roof and wall sums [r, g, b, area], kit grid and footprint box in model coordinates. */
async function measure(id) {
  if (models.has(id)) return models.get(id);
  const file = path.join(spec.dir, spec.models[id]);
  const doc = await io.read(file);
  const roof = [0, 0, 0, 0], wall = [0, 0, 0, 0], box = [Infinity, Infinity, -Infinity, -Infinity];
  let grid = null;
  for (const node of doc.getRoot().listNodes()) {
    const mesh = node.getMesh();
    if (!mesh) continue;
    let extras = {}, n = node;
    for (; n; n = n.getParentNode?.() ?? null) { const e = n.getExtras(); if (e?.surface || e?.material_key) { extras = e; break; } }
    // Merged draws carry one row per source object over index ranges: [{start, count, surface, facade_kit, ...}].
    let rows = extras.objects;
    if (typeof rows === 'string') { try { rows = JSON.parse(rows.replaceAll("'", '"')); } catch { rows = null; } }
    const parts = Array.isArray(rows) && rows.length ? rows.map(r => ({ start: r.start ?? 0, end: (r.start ?? 0) + (r.count ?? 0), ...look(r) }))
      : [{ start: 0, end: Infinity, ...look(extras) }];
    if (!parts.some(part => part.surface)) continue;
    for (const part of parts) if (part.program && !grid)
      grid = [part.program.bay ?? 0, part.program.storey ?? 0, part.program.opening?.[0] ?? 0, part.program.opening?.[1] ?? 0];
    const m = node.getWorldMatrix();
    for (const primitive of mesh.listPrimitives()) {
      const pos = primitive.getAttribute('POSITION'), uv = primitive.getAttribute('TEXCOORD_0');
      if (!pos) continue;
      const material = primitive.getMaterial(), factor = material?.getBaseColorFactor() ?? [1, 1, 1, 1];
      const texture = material?.getBaseColorTexture();
      let pixels = null;
      if (texture && (uv || parts.some(part => part.program))) {
        const key = texture.getURI() || texture.getName() || String(doc.getRoot().listTextures().indexOf(texture));
        const cacheKey = `${spec.models[id]}#${key}`;
        if (!decoded.has(cacheKey)) decoded.set(cacheKey, await decode(texture));
        pixels = decoded.get(cacheKey);
      }
      const index = primitive.getIndices()?.getArray() ?? Array.from({ length: pos.getCount() }, (_, i) => i);
      const a = [], b = [], c = [], ua = [], ub = [], uc = [];
      const world = p => [0, 1, 2].map(k => m[k] * p[0] + m[k + 4] * p[1] + m[k + 8] * p[2] + m[k + 12]);
      for (let t = 0; t + 2 < index.length; t += 3) {
        const p0 = world(pos.getElement(index[t], a)), p1 = world(pos.getElement(index[t + 1], b)), p2 = world(pos.getElement(index[t + 2], c));
        for (const p of [p0, p1, p2]) { box[0] = Math.min(box[0], p[0]); box[1] = Math.min(box[1], p[2]); box[2] = Math.max(box[2], p[0]); box[3] = Math.max(box[3], p[2]); }
        const u = [p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]], v = [p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]];
        const area = Math.hypot(u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]) / 2;
        if (!(area > 1e-6)) continue;
        const part = parts.find(q => t >= q.start && t < q.end);
        if (!part?.surface) continue;
        if (part.program && !part.kit) part.kit = kitPigment(part.program, pixels);
        let rgb = part.kit;
        if (!rgb && pixels) {
          const q0 = uv.getElement(index[t], ua), q1 = uv.getElement(index[t + 1], ub), q2 = uv.getElement(index[t + 2], uc);
          const s = (q0[0] + q1[0] + q2[0]) / 3, r = (q0[1] + q1[1] + q2[1]) / 3;
          const x = ((Math.floor((s - Math.floor(s)) * pixels.w) % pixels.w) + pixels.w) % pixels.w;
          const y = ((Math.floor((r - Math.floor(r)) * pixels.h) % pixels.h) + pixels.h) % pixels.h;
          const o = (y * pixels.w + x) * 4;
          rgb = [0, 1, 2].map(k => linear(pixels.px[o + k]) * factor[k]);
        }
        if (!rgb) continue;
        const sum = part.surface === 'roof' ? roof : wall;
        for (let k = 0; k < 3; k++) sum[k] += rgb[k] * area;
        sum[3] += area;
      }
    }
  }
  const result = { roof, wall, grid, box };
  models.set(id, result);
  return result;
}

const round = v => Math.round(v * 1e5) / 1e5;
const mean = s => s[3] > 0 ? [0, 1, 2].map(k => round(s[k] / s[3])) : null;
const buildings = {};
for (const placement of [...spec.placements].sort((a, b) => String(a.id).localeCompare(String(b.id)))) {
  if (!spec.models[placement.model]) continue;
  const r = await measure(placement.model), [px, , pz] = placement.position;
  buildings[placement.id] = {
    roof: mean(r.roof), wall: mean(r.wall), grid: r.grid ? r.grid.map(round) : null,
    box: Number.isFinite(r.box[0]) ? [r.box[0] + px, r.box[1] + pz, r.box[2] + px, r.box[3] + pz].map(round) : null };
}
process.stdout.write(JSON.stringify({ buildings }));
