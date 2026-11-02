// Measure authored ground travel from planted shoe vertices, without a browser or textures.
// node game/scripts/measure-strides.mjs game/public/assets/character/player.glb > strides.json
import fs from 'node:fs';
import { createHash } from 'node:crypto';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { AnimationMixer, Vector3 } from 'three';

const file = process.argv[2];
if (!file) throw new Error('Expected character GLB path');
const bytes = fs.readFileSync(file);
const jsonSize = bytes.readUInt32LE(12);
const json = JSON.parse(bytes.toString('utf8', 20, 20 + jsonSize));
const start = 28 + jsonSize, length = bytes.readUInt32LE(20 + jsonSize);
const binary = bytes.buffer.slice(bytes.byteOffset + start, bytes.byteOffset + start + length);
json.materials = json.materials.map(m => ({ name: m.name }));
json.textures = []; json.images = [];
json.extensionsRequired = json.extensionsRequired.filter(e => e !== 'KHR_texture_basisu');
json.extensionsUsed = json.extensionsUsed.filter(e => e !== 'KHR_texture_basisu');
const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).register(parser => ({
  name: 'motion_measure', beforeRoot: () => parser.cache.add('buffer:0', Promise.resolve(binary)),
}));
const gltf = await loader.parseAsync(json, '');
const model = gltf.scene, mixer = new AnimationMixer(model), shoes = [];
model.traverse(o => { if (o.isSkinnedMesh && /shoes|boots/i.test(o.material.name)) shoes.push(o); });
if (!shoes.length) throw new Error('No shoe meshes');
const result = { sourceSha256: createHash('sha256').update(bytes).digest('hex'), clips: {} };
const point = new Vector3();
for (const name of ['Walk_Loop', 'Walk_Formal_Loop', 'Jog_Fwd_Loop', 'Sprint_Loop']) {
  const clip = gltf.animations.find(c => c.name === name);
  if (!clip) throw new Error(`Missing ${name}`);
  mixer.stopAllAction(); const action = mixer.clipAction(clip).play();
  const count = Math.ceil(clip.duration * 120), frames = [], ankles = [], hipHeights = [];
  for (let f = 0; f <= count; f++) {
    action.time = f / count * clip.duration; mixer.update(0); model.updateMatrixWorld(true);
    const values = [];
    for (const mesh of shoes) {
      mesh.skeleton.update();
      for (let i = 0; i < mesh.geometry.attributes.position.count; i++) {
        mesh.getVertexPosition(i, point).applyMatrix4(mesh.matrixWorld); values.push(point.y, point.z);
      }
    }
    frames.push(values);
    hipHeights.push(model.getObjectByName('DEF-hips').getWorldPosition(new Vector3()).y);
    ankles.push(['L', 'R'].map(side => model.getObjectByName(`DEF-foot${side}`).getWorldPosition(new Vector3()).toArray()));
  }
  const speeds = [], dt = clip.duration / count;
  for (let f = 0; f < count; f++) {
    const a = frames[f], b = frames[f + 1];
    for (let i = 0; i < a.length; i += 2) if (a[i] < 0.025 && b[i] < 0.025) {
      const speed = (a[i + 1] - b[i + 1]) / dt;
      if (speed > 0.1) speeds.push(speed);
    }
  }
  speeds.sort((a, b) => a - b);
  if (!speeds.length) throw new Error(`No planted soles in ${name}`);
  result.clips[name] = { metresPerLoop: +(speeds[speeds.length >> 1] * clip.duration).toFixed(4),
    duration: clip.duration, samples: speeds.length,
    hips: { meanY: hipHeights.reduce((sum, y) => sum + y, 0) / hipHeights.length, minY: Math.min(...hipHeights), maxY: Math.max(...hipHeights) },
    speedP10: +speeds[Math.floor(speeds.length * 0.1)].toFixed(3), speedP90: +speeds[Math.floor(speeds.length * 0.9)].toFixed(3),
    ankles: ['L', 'R'].map((side, i) => ({ side, minY: Math.min(...ankles.map(a => a[i][1])), maxY: Math.max(...ankles.map(a => a[i][1])),
      minZ: Math.min(...ankles.map(a => a[i][2])), maxZ: Math.max(...ankles.map(a => a[i][2])), maxForwardPhase: ankles.map(a => a[i][2]).indexOf(Math.max(...ankles.map(a => a[i][2]))) / count })) };
}
console.log(JSON.stringify(result, null, 2));
