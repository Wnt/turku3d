import { test, expect } from 'vitest';
import * as THREE from 'three/webgpu';
import { playerRig } from '../fixtures/playerRig';
import { FootIK } from '../../src/actors/footIK';
import { soleMarkers } from '../../src/actors/soleMarkers';
import { LOCOMOTION, locomotionFrequency, locomotionStrideScale, locomotionWeights, locomotionBodyHeight } from '../../src/actors/locomotion';

for (const speed of [1.5, 3.6, 6]) test(`actual shoe soles keep support and toe roll at ${speed} m/s`, async () => {
  const gltf = await playerRig(), root = new THREE.Group(); root.add(gltf.scene);
  const mixer = new THREE.AnimationMixer(gltf.scene), weights = locomotionWeights(speed);
  const actions = LOCOMOTION.map((clip, i) => {
    const action = mixer.clipAction(gltf.animations.find(a => a.name === clip.clip)!).play();
    action.timeScale = 0; action.setEffectiveWeight(weights[i]); return action;
  });
  const ankles = ['L', 'R'].map(side => gltf.scene.getObjectByName(`DEF-foot${side}`)!);
  const markers = soleMarkers(gltf.scene, ankles);
  expect(markers.every(foot => foot.length >= 3)).toBe(true);
  const ik = new FootIK(root, gltf.scene, async origin => ({ toi: origin.y, point: { x: origin.x, y: 0, z: origin.z },
    normal: { x: 0, y: 1, z: 0 }, material: 1, tile: 'flat' }));
  let phase = 0, contacts = 0, worstContact = 0, penetration = 0, lowestPelvis = 0, lowHip = Infinity, highHip = -Infinity;
  let previous: THREE.Vector3[][] | null = null; const slip: number[] = [];
  for (let frame = 0; frame < 150; frame++) {
    ik.restore(); root.position.z = frame * speed / 60;
    phase = (phase + locomotionFrequency(speed, weights, 0) / 60) % 1;
    for (const action of actions) action.time = phase * action.getClip().duration;
    mixer.update(0); root.updateMatrixWorld(true);
    const low = (foot: typeof markers[number]) => Math.min(...foot.map(m => m.mesh.getVertexPosition(m.vertex, new THREE.Vector3()).applyMatrix4(m.mesh.matrixWorld).y));
    const before = markers.map(low);
    const hips = gltf.scene.getObjectByName('DEF-hips')!;
    const height = hips.getWorldPosition(new THREE.Vector3()).y;
    ik.update(1 / 60, true, false, locomotionStrideScale(weights, 0), locomotionBodyHeight(height, weights, 0));
    const finalHip = hips.getWorldPosition(new THREE.Vector3()).y;
    lowHip = Math.min(lowHip, finalHip); highHip = Math.max(highHip, finalHip);
    lowestPelvis = Math.min(lowestPelvis, (ik as unknown as { pelvis: number }).pelvis);
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    root.updateMatrixWorld(true);
    if (frame < 3) continue;
    const positions = markers.map(foot => foot.map(m => m.mesh.getVertexPosition(m.vertex, new THREE.Vector3()).applyMatrix4(m.mesh.matrixWorld)));
    if (previous) positions.forEach((foot, i) => {
      const speeds = foot.flatMap((p, j) => p.y < 0.025 && previous![i][j].y < 0.025 ? [Math.hypot(p.x - previous![i][j].x, p.z - previous![i][j].z) * 60] : []);
      if (speeds.length) slip.push(Math.min(...speeds));
    });
    previous = positions;
    markers.forEach((foot, i) => {
      const y = low(foot); penetration = Math.max(penetration, -y);
      if (before[i] < 0.025) { contacts++; worstContact = Math.max(worstContact, Math.abs(y)); }
    });
  }
  slip.sort((a, b) => a - b);
  console.log(JSON.stringify({ speed, contacts, worstContact, penetration, lowestPelvis, hipRange: highHip - lowHip, medianSlip: slip[slip.length >> 1], p90Slip: slip[Math.floor(slip.length * 0.9)] }));
  expect(slip[slip.length >> 1]).toBeLessThan(0.04);
  expect(contacts).toBeGreaterThan(5);
  expect(lowestPelvis).toBeGreaterThan(-0.06);
  expect(highHip - lowHip).toBeLessThan(speed === 1.5 ? 0.1 : 0.13);
  expect(penetration).toBeLessThan(0.025);
  expect(worstContact).toBeLessThan(0.025);
});
