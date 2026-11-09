#!/usr/bin/env node
// Rebakes the tree impostors of public/assets/nature/nature.glb as hemi-octahedral views (scripts/nature/
// octahedral.mjs) from the library's own full LOD meshes, and leaves every other mesh and texture as it is.
// The full build (build-nature-assets.mjs) bakes the same views, but it no longer reproduces the committed spruce
// meshes (trees.mjs is unchanged since they were committed; the cause is not known), so a full rebuild would also
// ship different spruces. The rebake keeps every other mesh and texture byte for byte.
//
//   node scripts/bake-tree-impostors.mjs [--src ~/turku3d/data/assets-src/nature] [--out public/assets/nature] [--previews DIR]
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { MeshoptEncoder, MeshoptDecoder } from 'meshoptimizer';
import { Img, savePng } from './nature/image.mjs';
import { foliageAtlas, barkAtlas } from './nature/textures.mjs';
import { MeshBuilder } from './nature/trees.mjs';
import { ktx2 } from './nature/ktx2.mjs';
import { atlasSize, bakeTree, fitTree, impostorQuad, shippedNormal } from './nature/octahedral.mjs';

const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const here = path.dirname(new URL(import.meta.url).pathname);
const src = path.resolve(opt('--src', path.join(os.homedir(), 'turku3d/data/assets-src/nature')));
const out = path.resolve(opt('--out', path.join(here, '../public/assets/nature')));
const previews = opt('--previews', path.join(src, 'build/previews'));
const cacheDir = path.join(src, 'build/ktx2');
const log = (...m) => console.log(`[impostors ${(performance.now() / 1000).toFixed(1)}s]`, ...m);
fs.mkdirSync(previews, { recursive: true });

const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder });
await MeshoptDecoder.ready; await MeshoptEncoder.ready;
const glbPath = path.join(out, 'nature.glb'), manifestPath = path.join(out, 'nature.json');
const doc = await io.read(glbPath);
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const root = doc.getRoot();
const nodes = new Map(root.listNodes().map(node => [node.getName(), node]));

log('composing foliage and bark textures');
const foliage = await foliageAtlas(src), bark = await barkAtlas(src);

/** A node's primitive as a raster.mjs mesh in asset space (the node transform carries the dequantization). */
function meshOf(node, prim) {
  const m = node.getWorldMatrix(), mesh = new MeshBuilder();
  const P = prim.getAttribute('POSITION'), N = prim.getAttribute('NORMAL'), T = prim.getAttribute('TEXCOORD_0'), C = prim.getAttribute('COLOR_0');
  const p = [], n = [], t = [], c = [];
  for (let i = 0; i < P.getCount(); i++) {
    P.getElement(i, p); N.getElement(i, n); T.getElement(i, t); C.getElement(i, c);
    const x = m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12], y = m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13], z = m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14];
    const nx = m[0] * n[0] + m[4] * n[1] + m[8] * n[2], ny = m[1] * n[0] + m[5] * n[1] + m[9] * n[2], nz = m[2] * n[0] + m[6] * n[1] + m[10] * n[2];
    const l = Math.hypot(nx, ny, nz) || 1;
    mesh.vertex([x, y, z], [nx / l, ny / l, nz / l], [t[0], t[1]], [c[0], c[1], c[2]], [0, 0, 0, 0]);
  }
  const I = prim.getIndices();
  for (let i = 0; i < I.getCount(); i++) mesh.indices.push(I.getScalar(i));
  return mesh;
}

const trees = Object.entries(manifest.assets).filter(([, a]) => a.kind === 'tree').map(([name]) => name);
const { width, height } = atlasSize(trees.length);
const albedoAtlas = new Img(width, height), normalAtlas = new Img(width, height);
const impostorMaterial = root.listMaterials().find(m => m.getName() === 'nature_impostor');
const buffer = root.listBuffers()[0];
const accessor = (array, type) => doc.createAccessor().setArray(array).setType(type).setBuffer(buffer);
for (const [index, name] of trees.entries()) {
  const info = manifest.assets[name], node = nodes.get(`${name}.lod0`);
  const parts = node.getMesh().listPrimitives().map(prim => {
    const material = prim.getMaterial().getName();
    return { mesh: meshOf(node, prim), texture: material === 'nature_leaves' ? foliage.atlas : bark.albedo, cutout: material === 'nature_leaves' };
  });
  const R = await fitTree(parts, info.species, info.height, info.radius);
  const top = Math.max(...parts.flatMap(p => p.mesh.positions.filter((_, i) => i % 3 === 1)));
  bakeTree(albedoAtlas, normalAtlas, index, parts, R, top);
  // The impostor LOD becomes the camera-facing quad.
  const quad = impostorQuad(MeshBuilder, index, R, top), lod = nodes.get(`${name}.lod${info.lods.length - 1}`);
  const mesh = lod.getMesh();
  for (const prim of mesh.listPrimitives()) prim.dispose();
  // No colour or wind: the views carry the crown's shading, and the far band does not sway.
  mesh.addPrimitive(doc.createPrimitive().setMaterial(impostorMaterial)
    .setAttribute('POSITION', accessor(new Float32Array(quad.positions), 'VEC3'))
    .setAttribute('NORMAL', accessor(new Float32Array(quad.normals), 'VEC3'))
    .setAttribute('TEXCOORD_0', accessor(new Float32Array(quad.uvs), 'VEC2'))
    .setAttribute('_IMPOSTOR', accessor(new Float32Array(quad.impostor), 'VEC4'))
    .setIndices(accessor(new Uint16Array(quad.indices), 'SCALAR')));
  lod.setMatrix([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
  info.lods[info.lods.length - 1] = { triangles: quad.triangles, vertices: quad.vertexCount, draws: ['nature_impostor'] };
  log(`tree ${name}: ${trees.length} trees, slot ${index}, R ${R.toFixed(2)} top ${top.toFixed(2)}`);
}
for (const accessorUnused of root.listAccessors()) if (!accessorUnused.listParents().some(p => p.propertyType !== 'Root')) accessorUnused.dispose();

await savePng(albedoAtlas, path.join(previews, 'impostor-albedo.png'));
await savePng(normalAtlas, path.join(previews, 'impostor-normal-depth.png'));
const textures = new Map(root.listTextures().map(t => [t.getName(), t]));
const replace = async (name, img, opts) => {
  const data = await ktx2(img, opts, cacheDir, log);
  textures.get(name).setImage(new Uint8Array(data));
  manifest.textures[name] = { width: img.w, height: img.h, bytes: data.length, format: opts.normal ? 'UASTC+zstd' : 'ETC1S' };
};
await replace('nature_impostor', albedoAtlas, { srgb: true, quality: 230 });
await replace('nature_impostor_normal', shippedNormal(normalAtlas), { srgb: false, normal: true });

await io.write(glbPath, doc);
const glb = fs.readFileSync(glbPath);
manifest.glb = { bytes: glb.length, sha256: createHash('sha256').update(glb).digest('hex') };
fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 1) + '\n');
const readme = path.join(out, 'README.md');
if (fs.existsSync(readme)) {
  const rows = Object.entries(manifest.assets).map(([name, a]) => `| \`${name}\` | ${a.species ?? a.kind} | ${a.height} | ` +
    [0, 1, 2].map(k => a.lods[k] ? `${a.lods[k].triangles.toLocaleString('en')}` : '-').join(' | ') + ' |');
  fs.writeFileSync(readme, fs.readFileSync(readme, 'utf8').replace(/<!-- table:begin -->[\s\S]*<!-- table:end -->/, `<!-- table:begin -->\n${rows.join('\n')}\n<!-- table:end -->`));
}
log(`nature.glb ${(glb.length / 1024 / 1024).toFixed(2)} MiB`);
