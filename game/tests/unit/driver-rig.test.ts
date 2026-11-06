import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { playerRig } from '../fixtures/playerRig';
import { FLEET } from '../../src/actors/fleet/catalog';
import { BODY, crown, steerFraction, steeringAngle, type DriverLayout } from '../../src/actors/driver/layout';
import { fleetDriverLayout } from '../../src/actors/driver/drivers';
import { DriverRig, DriverTemplate } from '../../src/actors/driver/rig';
import { DriverMotion, type MotionPose } from '../../src/actors/driver/motion';

const DEG = Math.PI / 180;
const still: MotionPose = { pitch: 0, roll: 0, bump: 0, headYaw: 0, headPitch: 0, reverse: 0, horn: 0, gLon: 0, gLat: 0 };

async function template() {
  const gltf = await playerRig();
  return new DriverTemplate(gltf.scene as unknown as THREE.Object3D, gltf.animations as unknown as THREE.AnimationClip[]);
}

test('the seated body constants match the shipped character in its Driving_Loop pose', async () => {
  const t = await template();
  const rig = new DriverRig(t), frame = new THREE.Group();
  frame.add(rig.model); frame.updateMatrixWorld(true);
  const at = (name: string) => rig.model.getObjectByName(name)!.getWorldPosition(new THREE.Vector3()).sub(t.hips);
  const close = (v: THREE.Vector3, expected: number[], tolerance: number) =>
    expected.forEach((e, i) => expect(Math.abs(v.getComponent(i) - e), `${v.toArray()} vs ${expected}`).toBeLessThan(tolerance));
  close(at('DEF-upper_armL'), BODY.shoulder, 0.03);
  close(at('DEF-spine001'), BODY.lumbar, 0.01);
  expect(at('DEF-forearmL').distanceTo(at('DEF-upper_armL'))).toBeCloseTo(BODY.upperArm, 2);
  expect(at('DEF-handL').distanceTo(at('DEF-forearmL'))).toBeCloseTo(BODY.forearm, 2);
  expect(Math.abs(at('DEF-head').y + 0.22 - BODY.crown[1])).toBeLessThan(0.02);
});

test('every fleet model seats its driver under the roof with both hands on the rim at rest and at full lock', async () => {
  const t = await template();
  const rig = new DriverRig(t), frame = new THREE.Group();
  frame.add(rig.model);
  const worst: { id: string; angle: number; error: number }[] = [];
  for (const model of FLEET) {
    const layout = fleetDriverLayout(model.id);
    expect(crown(layout.hips, layout.recline)[1], model.id).toBeLessThan(model.height - 0.05);
    expect(layout.hips[1], model.id).toBeGreaterThan(0.3);
    expect(Math.abs(layout.hips[2]), model.id).toBeLessThan(model.length / 2);
    for (const angle of [0, layout.wheel.lock / 2, -layout.wheel.lock / 2, layout.wheel.lock, -layout.wheel.lock]) {
      rig.pose(frame, layout, still, angle);
      worst.push({ id: model.id, angle: Math.round(angle / DEG), error: Math.max(rig.reach.left, rig.reach.right) });
    }
  }
  worst.sort((a, b) => b.error - a.error);
  console.log('worst wrist errors (m)', JSON.stringify(worst.slice(0, 6)));
  expect(worst[0].error).toBeLessThan(0.015);
});

test('the hands follow the rim as the wheel turns from the rest grips at 10 and 2; the right hand presses the horn', async () => {
  const t = await template();
  const rig = new DriverRig(t), frame = new THREE.Group();
  frame.add(rig.model);
  const layout = fleetDriverLayout('golf7');
  rig.pose(frame, layout, still, 0);
  const hand = (side: 'L' | 'R') => rig.model.getObjectByName(`DEF-hand${side}`)!.getWorldPosition(new THREE.Vector3());
  const c = new THREE.Vector3(...layout.wheel.centre);
  const left0 = hand('L'), right0 = hand('R');
  expect(left0.x).toBeGreaterThan(c.x + 0.1); expect(right0.x).toBeLessThan(c.x - 0.1);
  rig.pose(frame, layout, still, layout.wheel.lock);
  // Full lock left (95°): the outer (right) hand over the top at about 11 o'clock, the left one low on the left.
  expect(hand('R').x).toBeGreaterThan(c.x); expect(hand('R').y).toBeGreaterThan(hand('L').y + 0.15);
  expect(Math.max(rig.reach.left, rig.reach.right)).toBeLessThan(0.015);
  rig.pose(frame, layout, { ...still, horn: 1 }, 0);
  // The horn: the right hand on the hub, the left one stays on its grip.
  expect(hand('R').distanceTo(c)).toBeLessThan(0.13);
  expect(hand('L').distanceTo(left0)).toBeLessThan(0.005);
});

test('steering wheel angle: linear in the share of the lock, ±95° for cars (GTA), ±130° vans, ±180° buses', () => {
  const car = fleetDriverLayout('golf7'), van = fleetDriverLayout('euro-van'), bus = fleetDriverLayout('transit-bus');
  const at = (layout: DriverLayout, steer: number) => steeringAngle(layout.wheel, steerFraction(layout, [{ steer }, { steer }])) / DEG;
  expect(at(car, car.steerLock / 2)).toBeCloseTo(47.5, 5);
  expect(at(car, -car.steerLock)).toBeCloseTo(-95, 5);
  expect(at(car, -2 * car.steerLock)).toBeCloseTo(-95, 5);
  expect(at(van, van.steerLock)).toBeCloseTo(130, 5);
  expect(at(bus, bus.steerLock)).toBeCloseTo(180, 5);
  // 4WS trucks measure the inner wheel.
  expect(steerFraction({ steerLock: 0.7, steerFrom: 'inner' }, [{ steer: 0.7 }, { steer: 0.4 }])).toBeCloseTo(1, 5);
});

// A car moving along +Z (fleet frame = world) with the given speed history.
function drive(motion: DriverMotion, speed: (t: number) => number, until: number, from = 0, steer = 0, lateral?: (t: number) => number) {
  let pose = motion.pose;
  for (let t = from; t <= until + 1e-9; t += 1 / 60) {
    pose = motion.update({ t, v: { x: lateral?.(t) ?? 0, y: 0, z: speed(t) }, fwd: { x: 0, y: 0, z: 1 }, left: { x: 1, y: 0, z: 0 }, up: { x: 0, y: 1, z: 0 }, steer });
  }
  return pose;
}

test('braking at 1 g leans the torso forward 5°, with the measured 0.18 s response and no whiplash', () => {
  const m = new DriverMotion();
  const brake = (t: number) => t < 1 ? 22 : Math.max(0, 22 - 9.81 * (t - 1));
  drive(m, brake, 1);
  const early = drive(m, brake, 1.1, 1 + 1 / 60).pitch;
  const late = drive(m, brake, 1.9, 1.1 + 1 / 60).pitch;
  expect(late / DEG).toBeCloseTo(5, 1);
  expect(early / DEG).toBeLessThan(2.5);
  expect(m.pose.headPitch).toBeLessThan(0.5 * DEG);
});

test('a 50 km/h frontal crash pitches the head forward with the whiplash envelope and returns within 0.8 s', () => {
  const m = new DriverMotion();
  const v0 = 50 / 3.6, crash = (t: number) => t < 1 ? v0 : t < 1.1 ? v0 * (1 - (t - 1) / 0.1) : 0;
  drive(m, crash, 1);
  let peak = 0, peakAt = 0;
  for (let t = 1 + 1 / 60; t < 1.5; t += 1 / 60) {
    const p = drive(m, crash, t, t);
    if (p.headPitch > peak) { peak = p.headPitch; peakAt = t; }
  }
  expect(peak / DEG).toBeGreaterThan(22);
  expect(peak / DEG).toBeLessThanOrEqual(30.01);
  expect(peakAt - 1).toBeLessThan(0.35);
  expect(m.pose.pitch / DEG).toBeGreaterThan(5);
  const after = drive(m, crash, 2.0, 1.5);
  expect(after.headPitch / DEG).toBeLessThan(2);
});

test('a landing dips the hips up to 4 cm on the 3 Hz seat spring', () => {
  const m = new DriverMotion();
  let min = 0;
  for (let t = 0; t < 1.5; t += 1 / 60) {
    // Level, then 0.3 s of free fall, then the landing stops the fall within one frame.
    const vy = t < 0.5 ? 0 : t < 0.8 ? -9.81 * (t - 0.5) : 0;
    const p = m.update({ t, v: { x: 0, y: vy, z: 20 }, fwd: { x: 0, y: 0, z: 1 }, left: { x: 1, y: 0, z: 0 }, up: { x: 0, y: 1, z: 0 }, steer: 0 });
    min = Math.min(min, p.bump);
  }
  expect(min).toBeLessThan(-0.025);
  expect(min).toBeGreaterThanOrEqual(-0.04);
  expect(Math.abs(m.pose.bump)).toBeLessThan(0.005);
});

test('head looks into a turn (GTA: 42° at full lock, even at rest) and over the right shoulder when reversing', () => {
  const m = new DriverMotion();
  let p = m.pose;
  // Turning left at 30°/s with 30 % of the lock.
  for (let t = 0, yaw = 0; t < 1.5; t += 1 / 60, yaw += 30 * DEG / 60) {
    p = m.update({ t, v: { x: 8 * Math.sin(yaw), y: 0, z: 8 * Math.cos(yaw) }, fwd: { x: Math.sin(yaw), y: 0, z: Math.cos(yaw) },
      left: { x: Math.cos(yaw), y: 0, z: -Math.sin(yaw) }, up: { x: 0, y: 1, z: 0 }, steer: 0.3 });
  }
  expect(p.headYaw / DEG).toBeCloseTo(42 * 0.3 + 0.05 * 30, 0);
  expect(drive(new DriverMotion(), () => 0, 1.5, 0, -1).headYaw / DEG).toBeCloseTo(-42, 0);
  const r = new DriverMotion();
  const reversing = drive(r, () => -2, 1.2);
  expect(reversing.reverse).toBeGreaterThan(0.95);
  expect(reversing.headYaw).toBe(0);
  const stopping = drive(r, () => 0, 1.4, 1.2 + 1 / 60);
  expect(stopping.reverse).toBeGreaterThan(0.2);
  expect(drive(r, () => 0, 1.7, 1.4 + 1 / 60).reverse).toBe(0);
});

test('the horn hand blends to the hub over 0.15 s and back', () => {
  const m = new DriverMotion(), sample = (t: number, horn: boolean) => m.update({ t, v: { x: 0, y: 0, z: 0 },
    fwd: { x: 0, y: 0, z: 1 }, left: { x: 1, y: 0, z: 0 }, up: { x: 0, y: 1, z: 0 }, steer: 0, horn });
  let t = 0;
  for (; t < 0.1; t += 1 / 60) sample(t, true);
  expect(m.pose.horn).toBeGreaterThan(0.3); expect(m.pose.horn).toBeLessThan(1);
  for (; t < 0.5; t += 1 / 60) sample(t, true);
  expect(m.pose.horn).toBe(1);
  for (; t < 0.7; t += 1 / 60) sample(t, false);
  expect(m.pose.horn).toBe(0);
});

test('posture classes (GTA grips and recline for cars, a sports preset for low cars)', () => {
  const classes = new Map(FLEET.map(m => [m.id, fleetDriverLayout(m.id).cls]));
  expect(classes.get('carla-ford-mustang')).toBe('sports'); expect(classes.get('corolla-e180')).toBe('saloon');
  expect(classes.get('carla-nissan-micra')).toBe('hatch'); expect(classes.get('prado-2010')).toBe('suv');
  expect(classes.get('carla-tesla-cybertruck')).toBe('pickup'); expect(classes.get('euro-van')).toBe('van');
  expect(classes.get('transit-bus')).toBe('bus'); expect(classes.get('garbage-truck')).toBe('truck');
  expect(fleetDriverLayout('corolla-e180').wheel.grip / DEG).toBeCloseTo(58, 6);
  expect(fleetDriverLayout('carla-ford-mustang').recline / DEG).toBeCloseTo(27, 6);
});
