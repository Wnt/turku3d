// Retargets CC0 MakeHuman (CharacterCreator) outfit parts into the CC0 Quaternius UAL bind pose.
// Shared by prepare-character.mjs (the player) and bake-walkers.mjs (ambient walker bodies).
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { copyToDocument } from '@gltf-transform/functions';
import { Matrix4, Quaternion, Vector3, Matrix3 } from 'three';

/** CharacterCreator's pinned export revision and its body-zone bits (output/clothing.json `bodyZones`). */
export const CHARACTER_CREATOR_REVISION = '852e48f316b90a0937234886630e4fb4cdfd1f78';
export const BODY_ZONES = { feet: 1, legs: 2, hips: 4, torso: 8, coat: 16, shorts: 256, shirt: 512, hoodie: 1024,
  dress: 2048, jacket: 4096, boots: 8192 };
/** base_body morph target order (mesh extras `targetNames`). */
export const MORPHS = { female: 0, male: 1 };

const mapping = { Root: 'root', pelvis: 'DEF-hips', spine_01: 'DEF-spine.001', spine_02: 'DEF-spine.002', spine_03: 'DEF-spine.003', neck_01: 'DEF-neck', head: 'DEF-head' };
for (const [s, side] of [['l', 'L'], ['r', 'R']]) {
  for (const [from, to] of Object.entries({ clavicle: 'shoulder', upperarm: 'upper_arm', lowerarm: 'forearm', hand: 'hand', thigh: 'thigh', calf: 'shin', foot: 'foot', ball: 'toe' })) mapping[`${from}_${s}`] = `DEF-${to}.${side}`;
  for (const finger of ['index', 'middle', 'ring', 'pinky', 'thumb']) for (const digit of ['01', '02', '03']) mapping[`${finger}_${digit}_${s}`] = `DEF-${finger === 'thumb' ? '' : 'f_'}${finger}.${digit}.${side}`;
}
const position = m => new Vector3().setFromMatrixPosition(m);
const world = n => new Matrix4().fromArray(n.getWorldMatrix());
const axis = m => new Vector3(0, 1, 0).transformDirection(m);
// The MakeHuman torso has shorter spine segments than UAL. Moving its chest vertices
// to UAL's spine joint centres lifts the hoodie collar into the face in the bind pose.
// Preserve the source's anatomical height while keeping UAL's joints for animation.
const torso = new Set(['pelvis', 'spine_01', 'spine_02', 'spine_03', 'neck_01', 'head', 'clavicle_l', 'clavicle_r']);

/** Loads the UAL rig with only `clips` and without its own meshes. */
export async function loadRig(io, file, clips) {
  const doc = await io.read(file), root = doc.getRoot();
  for (const a of root.listAnimations()) if (!clips.has(a.getName())) a.dispose();
  for (const n of root.listNodes()) if (n.getMesh()) n.dispose();
  return doc;
}

/** Adds each `<part>.glb` of `sourceDir` to `doc`'s first scene, skinned to its UAL skin. `hidden` is the body-zone
 *  mask the outfit covers (fully covered triangles are removed); `morphs` are [target index, weight] pairs baked
 *  into every part. Returns the source hashes and the triangle count. */
export async function addOutfit(io, doc, { sourceDir, parts, hidden, morphs }) {
  const root = doc.getRoot(), scene = root.listScenes()[0];
  const skin = root.listSkins()[0], joints = skin.listJoints();
  const target = new Map(root.listNodes().map(n => [n.getName(), n]));
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
      const sourcePosition = position(from), targetPosition = position(to);
      if (torso.has(joint.getName())) targetPosition.y = sourcePosition.y * 1.035;
      return { from: sourcePosition, to: targetPosition, rotation, index: joints.indexOf(dest) };
    });
    for (const node of src.getRoot().listNodes().filter(n => n.getMesh())) {
      const mesh = node.getMesh(), nodeWorld = world(node), normalMatrix = new Matrix3().getNormalMatrix(nodeWorld);
      for (const primitive of mesh.listPrimitives()) {
        const pos = primitive.getAttribute('POSITION'), norm = primitive.getAttribute('NORMAL');
        const ids = primitive.getAttribute('JOINTS_0'), weights = primitive.getAttribute('WEIGHTS_0');
        const shapes = morphs.map(([index, weight]) => [primitive.listTargets()[index], weight]);
        const zone = primitive.getAttribute('_CCZONE');
        if (zone) {
          const indices = primitive.getIndices().getArray(), kept = [];
          // Hide only fully covered triangles; retain the garment boundary overlap.
          for (let i = 0; i < indices.length; i += 3) if (![0, 1, 2].every(k => (zone.getScalar(indices[i + k]) & hidden) !== 0)) kept.push(...indices.slice(i, i + 3));
          primitive.getIndices().setArray(new Uint32Array(kept));
        }
        for (let i = 0; i < pos.getCount(); i++) {
          const p = new Vector3().fromArray(pos.getElement(i, []));
          for (const [shape, weight] of shapes) if (shape?.getAttribute('POSITION')) p.addScaledVector(new Vector3().fromArray(shape.getAttribute('POSITION').getElement(i, [])), weight);
          p.applyMatrix4(nodeWorld);
          const n = new Vector3().fromArray(norm.getElement(i, []));
          for (const [shape, weight] of shapes) if (shape?.getAttribute('NORMAL')) n.addScaledVector(new Vector3().fromArray(shape.getAttribute('NORMAL').getElement(i, [])), weight);
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
  return { sources, triangles };
}
