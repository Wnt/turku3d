import { auditVisual } from './visual_audit.mjs';
import { snapshot, compareSnapshots } from './visual_snapshot.mjs';
import { serialize } from 'node:v8';
import { instanceFacadeComponents } from "./facade_components.mjs";
import { useTextureCache, retainTextureCache } from './texture_cache.mjs';
// Explicit GLB assembly: bind/encode textures first, then lossless meshopt. No L0 simplification or position quantization.
import fs from 'node:fs';
import { cachedEncoder } from './encode_cache.mjs';
import { batchVisual } from './batch.mjs';
import { createMaterialBinder } from './materials.mjs';
import { atlasFacades } from './facade_atlas.mjs';
import { atlasPropSigns } from './prop_atlas.mjs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { codecRunner, completed } from './codec.mjs';
import { compileTexture } from './texture_compile.mjs';
import { brotliCompressSync, constants as zlibConstants } from 'node:zlib';
import { createHash } from 'node:crypto';
import { splitVisualBuffers } from './split_buffers.mjs';
import { externalizeLibraryImages } from './shared_images.mjs';

const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const dep = (name) => import(pathToFileURL(require.resolve(name)).href);
const { Document, NodeIO } = await dep('@gltf-transform/core');
const { ALL_EXTENSIONS, EXTMeshGPUInstancing, EXTMeshoptCompression, KHRTextureBasisu, KHRTextureTransform } = await dep('@gltf-transform/extensions');
const { mergeDocuments, unpartition, prune, dedup, joinPrimitives, transformPrimitive, simplifyPrimitive, listTextureSlots } = await dep('@gltf-transform/functions');
const { MeshoptEncoder, MeshoptDecoder, MeshoptSimplifier } = await dep('meshoptimizer');
const sharp = (await dep('sharp')).default;
await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready, MeshoptSimplifier.ready]);
let meshCache, cacheRequest;
const meshEncoder = cachedEncoder(MeshoptEncoder,
  createHash('sha256').update(fs.readFileSync(require.resolve('meshoptimizer/encoder'))).digest('hex'),
  () => meshCache, file => useTextureCache(cacheRequest, file));
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.encoder': meshEncoder.encoder, 'meshopt.decoder': MeshoptDecoder });
const run = codecRunner();

async function encodeFacade(t, request) {
  const { id, width, height, slot } = t.getExtras().facade_map;
  const sourceSize = [t.getImage().readUInt32LE(20), t.getImage().readUInt32LE(24)];
  const density = request.facadeDensity?.[id] ?? (height <= 5 ? 72 : 48);
  const target = [Math.max(4, Math.min(sourceSize[0], Math.round(width * density / 4) * 4)),
    Math.max(4, Math.min(sourceSize[1], Math.round(height * density / 4) * 4))];
  if (target[0] === sourceSize[0] && target[1] === sourceSize[1]) return;
  const linear = slot !== 'albedo';
  // A measured codec comparison selects ETC1S for colour/ORM and UASTC for normals.
  const codec = slot === 'normal' ? 'uastc' : 'etc1s';
  const key = createHash('sha256').update('facade-density-v2').update(slot)
    .update(codec).update(JSON.stringify(target)).update(t.getImage()).digest('hex');
  const mapping = path.join(request.textureCache, key + '.hash');
  if (fs.existsSync(mapping)) {
    const cached = path.join(request.textureCache, fs.readFileSync(useTextureCache(request, mapping), 'utf8') + '.ktx2');
    if (fs.existsSync(cached)) { t.setImage(fs.readFileSync(useTextureCache(request, cached))); return; }
  }
  const workKey = `${key}.${process.pid}`;
  const source = path.join(request.textureCache, workKey + '.source.ktx2');
  const png = path.join(request.textureCache, workKey + '.png');
  const resized = path.join(request.textureCache, workKey + '.resized.png');
  let temp;
  fs.writeFileSync(source, t.getImage());
  try {
    await run('ktx', ['extract', '--transcode', 'rgb8', source, png]);
    await sharp(png).resize(target[0], target[1], { fit:'fill' }).png().toFile(resized);
    // Equal 6-bit pixels differ by at most 3/255 in each channel. This safely
    // shares visually indistinguishable maps from independent facade exports.
    const { data, info } = await sharp(resized).raw().toBuffer({ resolveWithObject:true });
    for (let i = 0; i < data.length; i++) data[i] >>= 2;
    const content = createHash('sha256').update('facade-near-v1').update(slot)
      .update(codec).update(JSON.stringify([info.width, info.height, info.channels]))
      .update(data).digest('hex');
    const dest = path.join(request.textureCache, content + '.ktx2');
    if (!fs.existsSync(dest)) {
      temp = dest + `.${process.pid}.tmp.ktx2`;
      await run('toktx', ['--t2', '--genmipmap', '--threads', '1', '--assign_oetf', linear ? 'linear' : 'srgb',
        '--assign_primaries', linear ? 'none' : 'bt709', '--encode', codec,
        ...(codec === 'etc1s' ? ['--qlevel', slot === 'albedo' ? '100' : '80'] : ['--uastc_quality', '2', '--zcmp', '18']),
        temp, resized]);
      await run('ktx', ['validate', '--gltf-basisu', temp]);
      fs.renameSync(temp, dest);
    }
    t.setImage(fs.readFileSync(useTextureCache(request, dest)));
    fs.writeFileSync(useTextureCache(request, mapping), content);
  } finally {
    for (const file of [source, png, resized, temp]) if (file && fs.existsSync(file)) fs.unlinkSync(file);
  }
}
async function assembleVisual(request) {
  cacheRequest = request;
  meshCache = path.join(request.textureCache, 'meshopt');
  const initialHits = meshEncoder.stats.hits, initialMisses = meshEncoder.stats.misses;
  const seconds = {};
  let stageTime = performance.now();
  const stage = name => { seconds[name] = (performance.now() - stageTime) / 1000; stageTime = performance.now(); };
  const doc = new Document();
  const scene = doc.createScene(request.tile);
  for (const filename of request.inputs) {
    const source = await io.read(filename);
    const map = mergeDocuments(doc, source);
    for (const s of source.getRoot().listScenes()) {
      for (const n of s.listChildren()) scene.addChild(map.get(n));
      map.get(s).dispose();
    }
  }
  doc.getRoot().setDefaultScene(scene);
  if (request.groundBinding) scene.setExtras({ ground_binding: request.groundBinding });

  const bindMaterial = createMaterialBinder(doc, request, { KHRTextureBasisu, KHRTextureTransform });
  // Landmarks ship their own mesh for these footprints; the buildings layer's shell goes.
  const replaced = new Set(request.replacedBuildings ?? []);
  for (const node of doc.getRoot().listNodes()) {
    const mesh = node.getMesh();
    if (!mesh) continue;
    const e = node.getExtras();
    if (e.layer === 'buildings' && replaced.has(String(e.building_id))) {
      node.setMesh(null); continue;
    }
    if (request.coarseTerrain && e.layer === 'ground' && !e.coarse_dtm) {
      node.setMesh(null); continue;
    }
    // Keep QA's object vocabulary while retaining the producer's stable identity.
    const kind = e.kind ?? ({ buildings:'building', buildings_outer:'building', structures:'prop', props:'prop' }[e.layer] ?? e.layer);
    node.setExtras({ ...e, id: e.object_id, kind, tile: request.tile, material: e.material_key });
    // Landmarks stay whole in coarse parents: they are what is seen from afar.
    if (request.level > 0 && ((e.layer !== 'ground' && e.layer !== 'buildings' && e.layer !== 'buildings_outer' && e.layer !== 'landmarks') || e.surface === 'curb' || e.kind === 'curb')) {
      node.setMesh(null); continue;
    }
    for (const prim of mesh.listPrimitives()) {
      if (prim.getMode() !== 4) throw new Error('Layer visuals must use triangles');
      if (request.level > 0) {
        if (e.layer === 'ground' && !e.coarse_dtm) {
          // Only coarse terrain is simplified. Lock borders to preserve adjacent ground coverage.
          simplifyPrimitive(prim, { simplifier: MeshoptSimplifier, ratio: 0.08 / request.level, error: 0.005, lockBorder: true });
        } else if (e.layer === 'buildings' && e.surface !== 'roof') {
          // Mixed building shells: keep upward-facing roof triangles, drop walls and floors.
          const pos = prim.getAttribute('POSITION');
          const idx = prim.getIndices();
          const a = pos.getArray(); const indices = idx?.getArray() ?? Uint32Array.from({ length: pos.getCount() }, (_, i) => i);
          const kept = []; const mat = node.getWorldMatrix();
          const world = (i) => [0, 1, 2].map(k => mat[k] * a[3*i] + mat[4+k] * a[3*i+1] + mat[8+k] * a[3*i+2] + mat[12+k]);
          for (let i = 0; i < indices.length; i += 3) {
            const [p, q, r] = [indices[i], indices[i+1], indices[i+2]].map(world);
            const u = q.map((x,k) => x-p[k]), v = r.map((x,k) => x-p[k]);
            const normal = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]];
            if (normal[1] > 0.25 * Math.hypot(...normal)) kept.push(...indices.slice(i, i+3));
          }
          if (!kept.length) { mesh.removePrimitive(prim); continue; }
          prim.setIndices(doc.createAccessor().setType('SCALAR').setArray(new Uint32Array(kept)).setBuffer(doc.getRoot().listBuffers()[0]));
        }
      }
      const material = prim.getMaterial()?.clone() ?? doc.createMaterial();
      prim.setMaterial(material);
      if (request.firstPass) {
        // First-pass geometry opens the boundary without waiting for texture codecs.
        const key = e.material_key ?? '';
        const color = key.includes('grass') ? [0.23, 0.32, 0.13, 1]
          : key.includes('road') || key.includes('asphalt') ? [0.20, 0.21, 0.22, 1]
            : e.layer === 'buildings' ? [0.65, 0.60, 0.53, 1] : [0.45, 0.44, 0.40, 1];
        material.setBaseColorTexture(null).setNormalTexture(null).setOcclusionTexture(null)
          .setMetallicRoughnessTexture(null).setBaseColorFactor(color).setMetallicFactor(0).setRoughnessFactor(0.9);
      } else bindMaterial(material, prim, e);
    }
    if (!mesh.listPrimitives().length) node.setMesh(null);
  }
  stage('binding');
  fs.mkdirSync(request.textureCache, { recursive:true });
  if (request.level === 0 && !request.firstPass) {
    // Drafts reuse the authored GPU-ready maps directly across tiles. Rebuilding
    // a per-tile atlas would decode and recompress the same source on arrival.
    request.facadeAtlas = request.draftTextures ? { pages:0, reused_sources:true }
      : await atlasFacades(doc, request, { sharp, run, KHRTextureBasisu });
    await atlasPropSigns(doc, sharp);
  }
  stage('facades');
  await doc.transform(dedup({ propertyTypes: ['Material'] }));
  instanceFacadeComponents(doc, scene, request.tile, EXTMeshGPUInstancing);
  await batchVisual(doc, scene, request.tile, { dedup, joinPrimitives, transformPrimitive });
  await doc.transform(prune({ keepLeaves: false, keepSolidTextures: true, keepAttributes: true, keepIndices: true }), unpartition());

  stage('batching');
  const before = snapshot(doc);
  if (!before.length) {
    if (request.level === 0) throw new Error('No L0 visual geometry');
    fs.writeFileSync(request.stats, JSON.stringify({ empty:true }));
    return; // A structures-only tile has no terrain/roof parent content.
  }
  stage('snapshot');
  // Cache encoded images independently of tiles. Coarse parents reuse the same GPU textures.
  fs.mkdirSync(request.textureCache, { recursive:true });
  for (const t of doc.getRoot().listTextures()) {
    if (!request.draftTextures && t.getExtras().facade_map && t.getMimeType() === 'image/ktx2') await encodeFacade(t, request);
  }
  await completed(doc.getRoot().listTextures().map(async t => {
    if (t.getMimeType() === 'image/ktx2') return;
    const slots = listTextureSlots(t);
    const srgb = slots.some(s => s === 'baseColorTexture' || s === 'emissiveTexture');
    if (srgb && slots.some(s => s !== 'baseColorTexture' && s !== 'emissiveTexture')) {
      throw new Error('One texture cannot serve both sRGB and linear material slots');
    }
    // Draft (coarse) tiles take the fastest ETC1S effort: half the encode time at the same size.
    const effort = srgb && request.draftTextures ? ['--clevel','0'] : [];
    // WebGPU rejects block-compressed textures whose sides are not multiples of 4, and one invalid texture
    // blacks out the whole frame. Per-building crops have arbitrary sizes; UVs are normalized, so stretch.
    const { width, height } = await sharp(Buffer.from(t.getImage())).metadata();
    const aligned = [width, height].map(v => Math.ceil(v / 4) * 4);
    const resize = aligned[0] !== width || aligned[1] !== height ? ['--resize', `${aligned[0]}x${aligned[1]}`] : [];
    const options = ['--t2','--genmipmap','--threads','1','--assign_oetf', srgb ? 'srgb' : 'linear', ...resize,
      '--encode', srgb ? 'etc1s' : 'uastc', ...(srgb ? ['--qlevel','128', ...effort] : ['--uastc_quality','2','--zcmp', request.draftTextures ? '6' : '18'])];
    const key = createHash('sha256').update('ktx-4.4.2-v1').update(JSON.stringify(options)).update(t.getImage()).digest('hex');
    const dest = await compileTexture(request, key, 'ktx2', async output => {
      const input = output + (t.getMimeType() === 'image/png' ? '.png' : '.jpg');
      fs.writeFileSync(input, t.getImage());
      try {
        await run('toktx', [...options, output, input]);
      } finally { fs.rmSync(input, { force:true }); }
    });
    t.setImage(fs.readFileSync(dest)).setMimeType('image/ktx2');
    doc.createExtension(KHRTextureBasisu).setRequired(true);
  }));
  stage('textures');
  const canonicalFacade = new Map();
  let facadeShares = 0;
  for (const t of doc.getRoot().listTextures()) {
    const slot = t.getExtras().facade_map?.slot;
    if (!slot) continue;
    const key = slot + ':' + createHash('sha256').update(t.getImage()).digest('hex');
    const first = canonicalFacade.get(key);
    if (!first) { canonicalFacade.set(key, t); continue; }
    for (const material of doc.getRoot().listMaterials()) {
      for (const property of ['BaseColor', 'Normal', 'Occlusion', 'MetallicRoughness']) {
        if (material[`get${property}Texture`]() === t) material[`set${property}Texture`](first);
      }
    }
    facadeShares++;
    t.dispose();
  }
  const sharedHashes = new Set(doc.getRoot().listTextures()
    .filter(t => request.draftTextures || t.getExtras().shared_library)
    .map(t => createHash('sha256').update(t.getImage()).digest('hex')));
  const packed = doc;
  packed.createExtension(EXTMeshoptCompression).setRequired(true)
    .setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.QUANTIZE });
  await io.write(request.output, packed);
  const shared = externalizeLibraryImages(request.output, sharedHashes,
    path.join(path.dirname(path.dirname(request.output)), 'shared'));
  const wireSize = bytes => brotliCompressSync(bytes, { params: {
    [zlibConstants.BROTLI_PARAM_QUALITY]: 5,
    [zlibConstants.BROTLI_PARAM_SIZE_HINT]: bytes.length,
  } }).length;
  let wire = wireSize(fs.readFileSync(request.output));
  if (request.draftTextures && wire > 8_000_000) {
    shared.push(...splitVisualBuffers(request.output, path.join(path.dirname(path.dirname(request.output)), 'shared')));
    wire = wireSize(fs.readFileSync(request.output));
  }
  let after = before, decoded = doc, error = null;
  let wallSurfaces = { state: 'pending' };
  if (!request.auditRoot) {
    decoded = await io.read(request.output);
    after = snapshot(decoded);
    wallSurfaces = auditVisual(decoded);
    error = compareSnapshots(before, after);
  } else {
    // Frozen expected geometry travels with the immutable published GLB. The
    // serial audit worker starts only after its manifest pointer is published.
    const hash = createHash('sha256').update(fs.readFileSync(request.output)).digest('hex');
    fs.mkdirSync(request.auditRoot, { recursive:true });
    const target = path.join(request.auditRoot, hash + '.snapshot');
    if (!fs.existsSync(target) && !fs.existsSync(path.join(request.auditRoot, hash + '.result.json'))) {
      const temp = target + `.${process.pid}.tmp`;
      try {
        fs.writeFileSync(temp, serialize(before));
        fs.renameSync(temp, target);
      } catch (error) { console.warn(`Audit snapshot failed: ${error.message}`); }
    }
  }
  stage('shipped_surface_audit');
  // Core getBounds sees only prototype geometry, not EXT_mesh_gpu_instancing transforms.
  const bounds = { min:[Infinity,Infinity,Infinity], max:[-Infinity,-Infinity,-Infinity] };
  for (const mesh of after) for (let i=0; i<mesh.v.length; i++) {
    bounds.min[i%3] = Math.min(bounds.min[i%3],mesh.v[i]);
    bounds.max[i%3] = Math.max(bounds.max[i%3],mesh.v[i]);
  }
  if (!Number.isFinite(bounds.min[1])) throw new Error('No visual geometry after assembly');
  stage('packaging');
  retainTextureCache(request);
  fs.writeFileSync(request.stats, JSON.stringify({ wall_surfaces:wallSurfaces, texture_compilation:request.textureCompilation, facade_atlas:request.facadeAtlas, mesh_cache: { hits:meshEncoder.stats.hits-initialHits, misses:meshEncoder.stats.misses-initialMisses }, seconds, tris:after.reduce((s,m)=>s+m.ix.length/3*m.instances,0), draws:after.length, h:[bounds.min[1],bounds.max[1]],
    bounds:[bounds.min,bounds.max], brotli_bytes:wire, max_error_m:error, shared_images:shared,
    textures:decoded.getRoot().listTextures().map(t=>t.getMimeType()), facade_shares:facadeShares }));

}
// `node visual.mjs requests.json` packages once; `--serve` keeps the modules loaded and packages one requests file per
// stdin line, answering `ok` or `error <JSON message>` per line (the coarse tile workers' persistent packager).
if (process.argv[2] === '--serve') {
  const { createInterface } = await import('node:readline');
  const { format } = await import('node:util');
  // stdout carries only the answers: glTF-Transform's logger and any other console output go to stderr.
  for (const level of ['log', 'info', 'debug', 'warn']) console[level] = (...args) => process.stderr.write(format(...args) + '\n');
  for await (const line of createInterface({ input: process.stdin })) {
    try {
      for (const request of JSON.parse(fs.readFileSync(line, 'utf8'))) await assembleVisual(request);
      process.stdout.write('ok\n');
    } catch (error) {
      process.stdout.write(`error ${JSON.stringify(String(error?.stack ?? error))}\n`);
    }
  }
} else {
  for (const request of JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))) await assembleVisual(request);
}
