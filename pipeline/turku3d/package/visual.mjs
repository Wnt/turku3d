// Explicit GLB assembly: bind/encode textures first, then lossless meshopt. No L0 simplify/join/quantize.
import fs from 'node:fs';
import { createMaterialBinder } from './materials.mjs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { brotliCompressSync, constants as zlibConstants } from 'node:zlib';
import { createHash } from 'node:crypto';

const require = createRequire(path.join(process.env.TURKU3D_GLTF_TOOLS ?? path.join(os.homedir(), 'turku3d/tools/npm'), 'package.json'));
const dep = (name) => import(pathToFileURL(require.resolve(name)).href);
const { Document, NodeIO } = await dep('@gltf-transform/core');
const { ALL_EXTENSIONS, EXTMeshoptCompression, KHRTextureBasisu, KHRTextureTransform } = await dep('@gltf-transform/extensions');
const { mergeDocuments, unpartition, prune, simplifyPrimitive, listTextureSlots } = await dep('@gltf-transform/functions');
const { MeshoptEncoder, MeshoptDecoder, MeshoptSimplifier } = await dep('meshoptimizer');
await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready, MeshoptSimplifier.ready]);
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder });
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
  const packed = doc;
  packed.createExtension(EXTMeshoptCompression).setRequired(true)
    .setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.QUANTIZE });
  await io.write(request.output, packed);
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
  if (request.level === 0 && wire > 8_000_000) throw new Error(`L0 brotli budget exceeded: ${wire}`);
  // Core getBounds sees only prototype geometry, not EXT_mesh_gpu_instancing transforms.
  const bounds = { min:[Infinity,Infinity,Infinity], max:[-Infinity,-Infinity,-Infinity] };
  for (const mesh of after) for (let i=0; i<mesh.v.length; i++) {
    bounds.min[i%3] = Math.min(bounds.min[i%3],mesh.v[i]);
    bounds.max[i%3] = Math.max(bounds.max[i%3],mesh.v[i]);
  }
  if (!Number.isFinite(bounds.min[1])) throw new Error('No visual geometry after assembly');
  fs.writeFileSync(request.stats, JSON.stringify({ tris:after.reduce((s,m)=>s+m.ix.length/3*m.instances,0), draws:after.length, h:[bounds.min[1],bounds.max[1]],
    bounds:[bounds.min,bounds.max], brotli_bytes:wire, max_error_m:error, textures:decoded.getRoot().listTextures().map(t=>t.getMimeType()) }));

}
for (const request of JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))) await assembleVisual(request);
