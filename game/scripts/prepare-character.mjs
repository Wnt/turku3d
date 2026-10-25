// CC0 MakeHuman outfit retargeted into the existing CC0 Quaternius UAL bind pose.
// Usage: npm run prepare-character -- <source-directory> <UAL.glb> <output.glb>
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS, EXTMeshoptCompression, KHRTextureBasisu } from '@gltf-transform/extensions';
import { copyToDocument, prune, dedup, resample, unpartition, weld } from '@gltf-transform/functions';
import { MeshoptEncoder } from 'meshoptimizer';
import { Matrix4, Quaternion, Vector3, Matrix3 } from 'three';

const [sourceDir, animationFile, output] = process.argv.slice(2);
if (!sourceDir || !animationFile || !output) throw new Error('usage: prepare-character.mjs <source-dir> <UAL.glb> <output.glb>');
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.encoder': MeshoptEncoder });
const doc = await io.read(animationFile), root = doc.getRoot(), scene = root.listScenes()[0];
const keep = new Set(['Idle_Loop', 'Walk_Loop', 'Walk_Formal_Loop', 'Jog_Fwd_Loop', 'Sprint_Loop', 'Driving_Loop', 'Jump_Start', 'Jump_Loop', 'Jump_Land']);
for (const a of root.listAnimations()) if (!keep.has(a.getName())) a.dispose();
const skin = root.listSkins()[0], joints = skin.listJoints(), buffer = root.listBuffers()[0];
for (const n of root.listNodes()) if (n.getMesh()) n.dispose();
const mapping = { Root: 'root', pelvis: 'DEF-hips', spine_01: 'DEF-spine.001', spine_02: 'DEF-spine.002', spine_03: 'DEF-spine.003', neck_01: 'DEF-neck', head: 'DEF-head' };
for (const [s, side] of [['l', 'L'], ['r', 'R']]) {
  for (const [from, to] of Object.entries({ clavicle: 'shoulder', upperarm: 'upper_arm', lowerarm: 'forearm', hand: 'hand', thigh: 'thigh', calf: 'shin', foot: 'foot', ball: 'toe' })) mapping[`${from}_${s}`] = `DEF-${to}.${side}`;
  for (const finger of ['index', 'middle', 'ring', 'pinky', 'thumb']) for (const digit of ['01', '02', '03']) mapping[`${finger}_${digit}_${s}`] = `DEF-${finger === 'thumb' ? '' : 'f_'}${finger}.${digit}.${side}`;
}
const target = new Map(root.listNodes().map(n => [n.getName(), n]));
const position = m => new Vector3().setFromMatrixPosition(m);
const world = n => new Matrix4().fromArray(n.getWorldMatrix());
const axis = m => new Vector3(0, 1, 0).transformDirection(m);
const parts = ['base_body', 'clothing_hoodie', 'clothing_jeans', 'clothing_shoes', 'hair_short02'];
const sources = [];
let triangles = 0;
for (const part of parts) {
  const file = path.join(sourceDir, `${part}.glb`);
  sources.push({ file: `${part}.glb`, sha256: createHash('sha256').update(fs.readFileSync(file)).digest('hex') });
  const src = await io.read(file), srcSkin = src.getRoot().listSkins()[0];
  const transforms = srcSkin.listJoints().map(joint => {
    const name = mapping[joint.getName()], dest = target.get(name);
    if (!dest) throw new Error(`Unmapped source bone: ${joint.getName()}`);
    const from = world(joint), to = world(dest);
    // Align the limb direction without importing either exporter's arbitrary bone roll.
    const rotation = new Quaternion().setFromUnitVectors(axis(from), axis(to));
    return { from: position(from), to: position(to), rotation, index: joints.indexOf(dest) };
  });
  for (const node of src.getRoot().listNodes().filter(n => n.getMesh())) {
    const mesh = node.getMesh(), nodeWorld = world(node), normalMatrix = new Matrix3().getNormalMatrix(nodeWorld);
    for (const primitive of mesh.listPrimitives()) {
      const pos = primitive.getAttribute('POSITION'), norm = primitive.getAttribute('NORMAL');
      const ids = primitive.getAttribute('JOINTS_0'), weights = primitive.getAttribute('WEIGHTS_0');
      const male = primitive.listTargets()[1];
      const zone = primitive.getAttribute('_CCZONE');
      if (zone) {
        const indices = primitive.getIndices().getArray(), kept = [];
        // Hide only fully covered triangles; retain the garment boundary overlap.
        const hidden = (1 | 2 | 1024);
        for (let i = 0; i < indices.length; i += 3) if (![0, 1, 2].every(k => (zone.getScalar(indices[i + k]) & hidden) !== 0)) kept.push(...indices.slice(i, i + 3));
        primitive.getIndices().setArray(new Uint32Array(kept));
      }
      for (let i = 0; i < pos.getCount(); i++) {
        const p = new Vector3().fromArray(pos.getElement(i, []));
        if (male?.getAttribute('POSITION')) p.addScaledVector(new Vector3().fromArray(male.getAttribute('POSITION').getElement(i, [])), 0.65);
        p.applyMatrix4(nodeWorld);
        const n = new Vector3().fromArray(norm.getElement(i, []));
        if (male?.getAttribute('NORMAL')) n.addScaledVector(new Vector3().fromArray(male.getAttribute('NORMAL').getElement(i, [])), 0.65);
        n.applyMatrix3(normalMatrix).normalize();
        const w = weights.getElement(i, []), boneIds = ids.getElement(i, []), out = new Vector3(), outNormal = new Vector3();
        for (let k = 0; k < 4; k++) {
          const t = transforms[boneIds[k]];
          if (t.index < 0) throw new Error('Retargeted joint is outside the UAL skin');
          out.addScaledVector(p.clone().sub(t.from).multiplyScalar(1.08).applyQuaternion(t.rotation).add(t.to), w[k]);
          outNormal.addScaledVector(n.clone().applyQuaternion(t.rotation), w[k]);
          boneIds[k] = t.index;
        }
        pos.setElement(i, out.toArray()); norm.setElement(i, outNormal.normalize().toArray()); ids.setElement(i, boneIds);
      }
      for (const morph of primitive.listTargets()) primitive.removeTarget(morph);
      for (const semantic of primitive.listSemantics()) if (semantic.startsWith('_')) primitive.setAttribute(semantic, null);
      triangles += primitive.getIndices().getCount() / 3;
    }
    mesh.setWeights([]).setExtras({});
    const copied = copyToDocument(doc, src, [mesh]).get(mesh);
    scene.addChild(doc.createNode(part).setMesh(copied).setSkin(skin));
  }
}
for (const material of root.listMaterials()) {
  material.setExtras({});
  if (material.getName() === 'Cloth_hoodie') material.setBaseColorFactor([0.12, 0.19, 0.14, 1]);
  if (material.getName() === 'Cloth_jeans') material.setBaseColorFactor([0.10, 0.15, 0.22, 1]);
}
await doc.transform(weld({ overwrite: true }), prune(), dedup(), resample(), unpartition());
const cache = path.join(sourceDir, 'ktx2'); fs.mkdirSync(cache, { recursive: true });
for (const texture of root.listTextures()) {
  const srgb = root.listMaterials().some(m => m.getBaseColorTexture() === texture || m.getEmissiveTexture() === texture);
  const hash = createHash('sha256').update(texture.getImage()).update(String(srgb)).digest('hex');
  const dest = path.join(cache, `${hash}.ktx2`);
  if (!fs.existsSync(dest)) {
    const input = path.join(cache, `${hash}.png`); fs.writeFileSync(input, texture.getImage());
    const args = ['--t2', '--genmipmap', '--threads', '2', '--assign_oetf', srgb ? 'srgb' : 'linear', '--assign_primaries', 'bt709', '--encode', srgb ? 'etc1s' : 'uastc', ...(srgb ? ['--qlevel', '180'] : ['--uastc_quality', '2', '--zcmp', '18']), dest, input];
    const result = spawnSync('toktx', args, { encoding: 'utf8' });
    if (result.status !== 0) throw new Error(`toktx: ${result.error ?? result.stderr}`);
  }
  texture.setImage(fs.readFileSync(dest)).setMimeType('image/ktx2');
}
doc.createExtension(KHRTextureBasisu).setRequired(true);
await MeshoptEncoder.ready;
doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.FILTER });
// All accessors share one buffer after the copied source documents are pruned.
for (const accessor of root.listAccessors()) accessor.setBuffer(buffer);
await io.write(output, doc);
console.log(JSON.stringify({ triangles, bytes: fs.statSync(output).size, clips: root.listAnimations().map(a => a.getName()), sources }, null, 2));
