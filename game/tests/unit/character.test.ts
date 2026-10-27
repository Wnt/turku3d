import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { CharacterInterpolation } from '../../src/actors/interpolation';
import { FootIK, solveLeg } from '../../src/actors/footIK';
import type { CharacterState } from '../../src/physics/protocol';
const state = (x: number): CharacterState => ({ pos: { x, y: 2, z: 0 }, vel: { x: 1, y: 0, z: 0 }, grounded: true, enabled: true, groundMaterial: 1 });
for (const hz of [120, 240]) test(`interpolation advances evenly at ${hz} Hz and ignores duplicate ticks`, () => {
  const i = new CharacterInterpolation();
  i.push(state(1 / 60), state(0), 1, 0, 0);
  for (let frame = 0; frame <= hz / 60; frame++) {
    const now = frame / hz;
    i.push(state(1 / 60), state(0), 1, 0, now);
    expect(i.sample(now)!.pos.x).toBeCloseTo(now, 6);
  }
  i.push(state(30), state(1 / 60), 2, 0, 1 / 60);
  expect(i.sample(1 / 60)!.pos.x).toBe(30);
});
test('two-bone IK reaches both sides of a curb without changing bone lengths', () => {
  const root = new THREE.Group(), hip = new THREE.Bone(), knee = new THREE.Bone(), ankle = new THREE.Bone();
  root.add(hip); hip.add(knee); knee.add(ankle); hip.position.y = 0.8; knee.position.y = -0.4; ankle.position.y = -0.4;
  root.rotation.y = 0.7;
  for (const height of [0.01, 0.12, 0.16, 0.32]) {
    hip.quaternion.identity(); knee.quaternion.identity(); root.updateMatrixWorld(true);
    const target = new THREE.Vector3(0.05, height, 0.1);
    solveLeg(hip, knee, ankle, target, new THREE.Vector3(0, 0, 1));
    expect(ankle.getWorldPosition(new THREE.Vector3()).distanceTo(target)).toBeLessThan(0.001);
    expect(knee.position.length()).toBeCloseTo(0.4); expect(ankle.position.length()).toBeCloseTo(0.4);
  }
});

test('foot IK places feet independently across a curb and lowers the pelvis on descent', async () => {
  const root = new THREE.Group(), model = new THREE.Group(), hips = new THREE.Bone();
  root.add(model); model.add(hips); hips.name = 'DEF-hips'; hips.position.y = 0.8;
  for (const [side, x] of [['L', -0.09], ['R', 0.09]] as const) {
    const hip = new THREE.Bone(), knee = new THREE.Bone(), ankle = new THREE.Bone();
    hip.name = `DEF-thigh${side}`; knee.name = `DEF-shin${side}`; ankle.name = `DEF-foot${side}`;
    hips.add(hip); hip.add(knee); knee.add(ankle);
    hip.position.x = x; knee.position.y = -0.4; ankle.position.y = -0.4;
  }
  let lowerGround = 0;
  const ik = new FootIK(root, model, async origin => ({ toi: 0.5, point: { x: origin.x, y: origin.x < 0 ? lowerGround : 0.12, z: origin.z },
    normal: { x: 0, y: 1, z: 0 }, material: 2, tile: 'curb' }));
  root.position.y = 0.12;
  ik.update(1 / 60, true, false);
  await Promise.resolve(); await Promise.resolve();
  ik.restore(); ik.update(0.2, true, false);
  expect(ik.contacts).toHaveLength(2);
  expect(ik.contacts[0].ankle[1]).toBeCloseTo(0, 3);
  expect(ik.contacts[1].ankle[1]).toBeCloseTo(0.12, 3);
  expect(hips.position.y).toBeLessThan(0.72);
  ik.restore(); expect(hips.position.y).toBeCloseTo(0.8);
  // A trailing foot crosses a descending tread edge between frames: pelvis damping must not
  // leave the stance target beyond the leg's reach while the body catches up.
  lowerGround = -0.08;
  for (let frame = 0; frame < 3; frame++) {
    ik.restore(); ik.update(1 / 60, true, true);
    await Promise.resolve(); await Promise.resolve();
  }
  expect(ik.contacts[0].ankle[1]).toBeCloseTo(-0.08, 3);
});
