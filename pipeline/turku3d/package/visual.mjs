// Explicit GLB assembly: bind/encode textures first, then lossless meshopt. No L0 simplification or position quantization.
import fs from 'node:fs';
import { batchVisual } from './batch.mjs';
import { createMaterialBinder } from './materials.mjs';
import { atlasFacades } from './facade_atlas.mjs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { brotliCompressSync, constants as zlibConstants } from 'node:zlib';
import { createHash } from 'node:crypto';
import { externalizeLibraryImages } from './shared_images.mjs';

const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const dep = (name) => import(pathToFileURL(require.resolve(name)).href);
const { Document, NodeIO } = await dep('@gltf-transform/core');
const { ALL_EXTENSIONS, EXTMeshoptCompression, KHRTextureBasisu, KHRTextureTransform } = await dep('@gltf-transform/extensions');
const { mergeDocuments, unpartition, prune, dedup, joinPrimitives, transformPrimitive, simplifyPrimitive, listTextureSlots } = await dep('@gltf-transform/functions');
const { MeshoptEncoder, MeshoptDecoder, MeshoptSimplifier } = await dep('meshoptimizer');
const sharp = (await dep('sharp')).default;
await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready, MeshoptSimplifier.ready]);
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder });
function run(tool, args) {
  const proc = spawnSync(tool, args, { encoding:'utf8' });
  if (proc.status !== 0) throw new Error(`${tool}: ${proc.error?.message ?? proc.stderr}\n${proc.stdout ?? ''}`);
}

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
    const cached = path.join(request.textureCache, fs.readFileSync(mapping, 'utf8') + '.ktx2');
    if (fs.existsSync(cached)) { t.setImage(fs.readFileSync(cached)); return; }
  }
  const workKey = `${key}.${process.pid}`;
  const source = path.join(request.textureCache, workKey + '.source.ktx2');
  const png = path.join(request.textureCache, workKey + '.png');
  const resized = path.join(request.textureCache, workKey + '.resized.png');
  let temp;
  fs.writeFileSync(source, t.getImage());
  try {
    run('ktx', ['extract', '--transcode', 'rgb8', source, png]);
    run('sips', ['-z', String(target[1]), String(target[0]), png, '--out', resized]);
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
      run('toktx', ['--t2', '--genmipmap', '--threads', '2', '--assign_oetf', linear ? 'linear' : 'srgb',
        '--assign_primaries', linear ? 'none' : 'bt709', '--encode', codec,
        ...(codec === 'etc1s' ? ['--qlevel', slot === 'albedo' ? '100' : '80'] : ['--uastc_quality', '2', '--zcmp', '18']),
        temp, resized]);
      run('ktx', ['validate', '--gltf-basisu', temp]);
      fs.renameSync(temp, dest);
    }
    t.setImage(fs.readFileSync(dest));
    fs.writeFileSync(mapping, content);
  } finally {
    for (const file of [source, png, resized, temp]) if (file && fs.existsSync(file)) fs.unlinkSync(file);
  }
}
async function assembleVisual(request) {
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
  for (const node of doc.getRoot().listNodes()) {
    const mesh = node.getMesh();
    if (!mesh) continue;
    const e = node.getExtras();
    if (request.coarseTerrain && e.layer === 'ground' && !e.coarse_dtm) {
      node.setMesh(null); continue;
    }
    // Keep QA's object vocabulary while retaining the producer's stable identity.
    const kind = e.kind ?? ({ buildings:'building', structures:'prop', props:'prop' }[e.layer] ?? e.layer);
    node.setExtras({ ...e, id: e.object_id, kind, tile: request.tile, material: e.material_key });
    if (request.level > 0 && ((e.layer !== 'ground' && e.layer !== 'buildings') || e.surface === 'curb' || e.kind === 'curb')) {
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
      bindMaterial(material, prim, e);
    }
    if (!mesh.listPrimitives().length) node.setMesh(null);
  }
  fs.mkdirSync(request.textureCache, { recursive:true });
  if (request.level === 0) {
    await atlasFacades(doc, request, { sharp, run, KHRTextureBasisu });
  }
  await batchVisual(doc, scene, request.tile, { dedup, joinPrimitives, transformPrimitive });
  await doc.transform(prune({ keepLeaves: false, keepSolidTextures: true, keepAttributes: true, keepIndices: true }), unpartition());

  // Snapshot world positions and triangle topology before codecs; validates each actual packaged tile.
  function snapshot(d) {
    const result = [];
    for (const n of d.getRoot().listNodes()) {
      if (!n.getMesh()) continue;
      const mat = n.getWorldMatrix();
      const batch = n.getExtension('EXT_mesh_gpu_instancing');
      const count = batch ? batch.listAttributes()[0].getCount() : 1;
      const transforms = Array.from({ length: count }, (_, i) => ({
        t: batch?.getAttribute('TRANSLATION')?.getElement(i, []) ?? [0,0,0],
        r: batch?.getAttribute('ROTATION')?.getElement(i, []) ?? [0,0,0,1],
        s: batch?.getAttribute('SCALE')?.getElement(i, []) ?? [1,1,1],
      }));
      for (const p of n.getMesh().listPrimitives()) {
        const a = p.getAttribute('POSITION');
        const v = [];
        for (const { t, r, s } of transforms) for (let i=0; i<a.getCount(); i++) {
          const q = a.getElement(i, []).map((x,k) => x*s[k]);
          // Quaternion rotation, then translation, then the complete node world matrix.
          const uv = [r[1]*q[2]-r[2]*q[1], r[2]*q[0]-r[0]*q[2], r[0]*q[1]-r[1]*q[0]];
          const uuv = [r[1]*uv[2]-r[2]*uv[1], r[2]*uv[0]-r[0]*uv[2], r[0]*uv[1]-r[1]*uv[0]];
          const local = q.map((x,k) => x+2*(r[3]*uv[k]+uuv[k])+t[k]);
          v.push(...[0,1,2].map(k => mat[k]*local[0]+mat[4+k]*local[1]+mat[8+k]*local[2]+mat[12+k]));
        }
        const ix = Array.from(p.getIndices()?.getArray() ?? Uint32Array.from({length:a.getCount()}, (_,i)=>i));
        // Index codec may rotate a triangle cyclically without changing winding.
        for (let i=0;i<ix.length;i+=3) {
          const t=ix.slice(i,i+3), j=t.indexOf(Math.min(...t)); ix.splice(i,3,...t.slice(j),...t.slice(0,j));
        }
        result.push({ id:n.getExtras().object_id, v, ix, instances:count });
      }
    }
    return result;
  }
  const before = snapshot(doc);
  if (!before.length) {
    if (request.level === 0) throw new Error('No L0 visual geometry');
    fs.writeFileSync(request.stats, JSON.stringify({ empty:true }));
    return; // A structures-only tile has no terrain/roof parent content.
  }
  // Cache encoded images independently of tiles. Coarse parents reuse the same GPU textures.
  fs.mkdirSync(request.textureCache, { recursive:true });
  for (const t of doc.getRoot().listTextures()) {
    if (t.getExtras().facade_map && t.getMimeType() === 'image/ktx2') { await encodeFacade(t, request); continue; }
    if (t.getMimeType() === 'image/ktx2') continue;
    const slots = listTextureSlots(t);
    const srgb = slots.some(s => s === 'baseColorTexture' || s === 'emissiveTexture');
    if (srgb && slots.some(s => s !== 'baseColorTexture' && s !== 'emissiveTexture')) {
      throw new Error('One texture cannot serve both sRGB and linear material slots');
    }
    const options = ['--t2','--genmipmap','--threads','2','--assign_oetf', srgb ? 'srgb' : 'linear',
      '--encode', srgb ? 'etc1s' : 'uastc', ...(srgb ? ['--qlevel','128'] : ['--uastc_quality','2','--zcmp','18'])];
    const key = createHash('sha256').update('ktx-4.4.2-v1').update(JSON.stringify(options)).update(t.getImage()).digest('hex');
    const dest = path.join(request.textureCache, key + '.ktx2');
    if (!fs.existsSync(dest)) {
      const input = path.join(path.dirname(request.output), key + (t.getMimeType() === 'image/png' ? '.png' : '.jpg'));
      fs.writeFileSync(input, t.getImage());
      const temp = dest + `.${process.pid}.tmp.ktx2`;
      const proc = spawnSync('toktx', [...options, temp, input], { encoding:'utf8' });
      if (proc.status !== 0) throw new Error(`toktx: ${proc.error?.message ?? proc.stderr}\n${proc.stdout ?? ""}`);
      fs.renameSync(temp, dest);
      fs.unlinkSync(input);
    }
    t.setImage(fs.readFileSync(dest)).setMimeType('image/ktx2');
    doc.createExtension(KHRTextureBasisu).setRequired(true);
  }
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
    .filter(t => t.getExtras().shared_library)
    .map(t => createHash('sha256').update(t.getImage()).digest('hex')));
  const packed = doc;
  packed.createExtension(EXTMeshoptCompression).setRequired(true)
    .setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.QUANTIZE });
  await io.write(request.output, packed);
  const shared = externalizeLibraryImages(request.output, sharedHashes,
    path.join(path.dirname(path.dirname(request.output)), 'shared'));
  const decoded = await io.read(request.output);
  const after = snapshot(decoded);
  let error = 0;
  if (before.length !== after.length) throw new Error('Mesh count changed during compression');
  for (let i=0;i<before.length;i++) {
    if (before[i].id !== after[i].id || before[i].v.length !== after[i].v.length || JSON.stringify(before[i].ix)!==JSON.stringify(after[i].ix)) throw new Error('Geometry topology changed during compression');
    for (let j=0;j<before[i].v.length;j++) error=Math.max(error, Math.abs(before[i].v[j]-after[i].v[j]));
  }
  if (error > 0.002) throw new Error(`Visual precision exceeds 2 mm: ${error}`);
  const bytes = fs.readFileSync(request.output);
  // Match tools/dev/serve-dist.mjs: the budget is the size actually sent by our server.
  const wire = brotliCompressSync(bytes, { params: {
    [zlibConstants.BROTLI_PARAM_QUALITY]: 5,
    [zlibConstants.BROTLI_PARAM_SIZE_HINT]: bytes.length,
  } }).length;
  // Core getBounds sees only prototype geometry, not EXT_mesh_gpu_instancing transforms.
  const bounds = { min:[Infinity,Infinity,Infinity], max:[-Infinity,-Infinity,-Infinity] };
  for (const mesh of after) for (let i=0; i<mesh.v.length; i++) {
    bounds.min[i%3] = Math.min(bounds.min[i%3],mesh.v[i]);
    bounds.max[i%3] = Math.max(bounds.max[i%3],mesh.v[i]);
  }
  if (!Number.isFinite(bounds.min[1])) throw new Error('No visual geometry after assembly');
  fs.writeFileSync(request.stats, JSON.stringify({ tris:after.reduce((s,m)=>s+m.ix.length/3*m.instances,0), draws:after.length, h:[bounds.min[1],bounds.max[1]],
    bounds:[bounds.min,bounds.max], brotli_bytes:wire, max_error_m:error, shared_images:shared,
    textures:decoded.getRoot().listTextures().map(t=>t.getMimeType()), facade_shares:facadeShares }));

}
for (const request of JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))) await assembleVisual(request);
