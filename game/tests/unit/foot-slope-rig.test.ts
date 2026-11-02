import { test, expect } from 'vitest';
import * as T from 'three/webgpu';
import { playerRig } from '../fixtures/playerRig';
import { FootIK } from '../../src/actors/footIK';
import { soleMarkers } from '../../src/actors/soleMarkers';
import { LOCOMOTION, locomotionFrequency, locomotionWeights } from '../../src/actors/locomotion';

for (const [along, across] of [[0.2, 0], [-0.2, 0], [0, 0.18]]) {
  test(`shipped shoe soles follow slope ${along}/${across} without penetrating it`, async () => {
    const gltf = await playerRig(), root = new T.Group(); root.add(gltf.scene);
    const mixer = new T.AnimationMixer(gltf.scene);
    const action = mixer.clipAction(gltf.animations.find(a => a.name === LOCOMOTION[1].clip)!).play();
    action.timeScale = 0;
    const height = (x: number, z: number) => x * across + z * along;
    const normal = new T.Vector3(-across, 1, -along).normalize();
    const markers = soleMarkers(gltf.scene, ['L', 'R'].map(side => gltf.scene.getObjectByName(`DEF-foot${side}`)!));
    const ik = new FootIK(root, gltf.scene, async p => ({ toi: p.y - height(p.x, p.z),
      point: { x: p.x, y: height(p.x, p.z), z: p.z }, normal, material: 1, tile: 'slope' }));
    let phase = 0, penetration = 0, support = 0;
    for (let frame = 0; frame < 120; frame++) {
      ik.restore(); root.position.set(0, height(0, frame * 1.5 / 60), frame * 1.5 / 60);
      phase = (phase + locomotionFrequency(1.5, locomotionWeights(1.5), 0) / 60) % 1;
      action.time = phase * action.getClip().duration; mixer.update(0); root.updateMatrixWorld(true);
      ik.update(1 / 60, true, false);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); root.updateMatrixWorld(true);
      if (frame < 3) continue;
      for (const foot of markers) {
        const gaps = foot.map(m => { const p = m.mesh.getVertexPosition(m.vertex, new T.Vector3()).applyMatrix4(m.mesh.matrixWorld); return p.y - height(p.x, p.z); });
        const gap = Math.min(...gaps); penetration = Math.max(penetration, -gap); if (Math.abs(gap) < 0.025) support++;
      }
    }
    expect(penetration).toBeLessThan(0.025); expect(support).toBeGreaterThan(30);
  });
}
