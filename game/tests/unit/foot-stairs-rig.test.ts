import { test, expect } from 'vitest';
import * as THREE from 'three/webgpu';
import * as R from '@dimforge/rapier3d';
import { playerRig } from '../fixtures/playerRig';
import { FootIK } from '../../src/actors/footIK';
import { soleMarkers } from '../../src/actors/soleMarkers';
import { STAIR_STRIDE } from '../../src/actors/locomotion';
import { PhysicsWorld } from '../../src/physics/world';
import { GROUP } from '../../src/shared/col/materials';

test('actual shoes clear each 15cm stair tread while the production capsule climbs', async () => {
  const pw = new PhysicsWorld(R), groups = (GROUP.STATIC_SOLID << 16) | GROUP.CHARACTER | 0xff00;
  pw.world.createCollider(R.ColliderDesc.cuboid(10, 0.2, 10).setTranslation(0, -0.2, 0).setCollisionGroups(groups));
  for (let i = 0; i < 8; i++) pw.world.createCollider(R.ColliderDesc.cuboid(1.5, (i + 1) * 0.075, 0.17)
    .setTranslation(0, (i + 1) * 0.075, 0.3 + i * 0.34).setCollisionGroups(groups));
  pw.world.step(); pw.spawnCharacter({ x: 0, y: 0, z: -1 });
  const gltf = await playerRig(), root = new THREE.Group(); root.add(gltf.scene);
  const mixer = new THREE.AnimationMixer(gltf.scene), action = mixer.clipAction(gltf.animations.find(a => a.name === 'Walk_Formal_Loop')!).play();
  action.timeScale = 0;
  const ankles = ['L', 'R'].map(side => gltf.scene.getObjectByName(`DEF-foot${side}`)!);
  const markers = soleMarkers(gltf.scene, ankles);
  const ik = new FootIK(root, gltf.scene, async origin => pw.raycast(origin, { x: 0, y: -1, z: 0 }, 1.5, 'solid'));
  const height = (z: number) => z < 0.13 || z >= 2.85 ? 0 : Math.min(8, Math.floor((z - 0.13) / 0.34) + 1) * 0.15;
  let phase = 0, penetration = 0, top = 0;
  try {
    for (let frame = 0; frame < 260; frame++) {
      pw.fixedStep({ vx: 0, vz: 0.9, jump: false }, null);
      const s = pw.characterState()!; top = Math.max(top, s.pos.y);
      ik.restore(); root.position.x = s.pos.x; root.position.z = s.pos.z;
      root.position.y += (s.pos.y - root.position.y) * (1 - Math.exp(-20 / 60));
      phase = (phase + Math.hypot(s.vel.x, s.vel.z) / STAIR_STRIDE / 60) % 1;
      action.time = phase * action.getClip().duration; mixer.update(0); root.updateMatrixWorld(true);
      ik.update(1 / 60, s.grounded, true);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
      root.updateMatrixWorld(true);
      if (frame < 4) continue;
      for (const foot of markers) for (const m of foot) {
        const p = m.mesh.getVertexPosition(m.vertex, new THREE.Vector3()).applyMatrix4(m.mesh.matrixWorld);
        penetration = Math.max(penetration, height(p.z) - p.y);
      }
    }
  } finally { pw.world.free(); }
  console.log(JSON.stringify({ top, penetration }));
  expect(top).toBeGreaterThan(0.9);
  expect(penetration).toBeLessThan(0.025);
});
