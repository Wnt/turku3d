import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { playerRig } from '../fixtures/playerRig';
import { FLEET } from '../../src/actors/fleet/catalog';
import { BODY, crown, steeringAngle, type DriverLayout } from '../../src/actors/driver/layout';
import { fleetDriverLayout } from '../../src/actors/driver/drivers';
import { DriverRig, DriverTemplate } from '../../src/actors/driver/rig';
import { DriverMotion, type MotionPose } from '../../src/actors/driver/motion';

const DEG = Math.PI / 180;
const still: MotionPose = { pitch: 0, roll: 0, bump: 0, headYaw: 0, headPitch: 0, reverse: 0, gLon: 0, gLat: 0 };

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
    for (const angle of [0, 90 * DEG, -90 * DEG, layout.wheel.cap, -layout.wheel.cap]) {
      rig.pose(frame, layout, still, angle);
      worst.push({ id: model.id, angle: Math.round(angle / DEG), error: Math.max(rig.reach.left, rig.reach.right) });
    }
  }
  worst.sort((a, b) => b.error - a.error);
  console.log('worst wrist errors (m)', JSON.stringify(worst.slice(0, 6)));
  expect(worst[0].error).toBeLessThan(0.015);
});

test('the hands follow the rim as the wheel turns, the rest grips at about ±70°', async () => {
  const t = await template();
  const rig = new DriverRig(t), frame = new THREE.Group();
  frame.add(rig.model);
  const layout = fleetDriverLayout('golf7');
  rig.pose(frame, layout, still, 0);
  const hand = (side: 'L' | 'R') => rig.model.getObjectByName(`DEF-hand${side}`)!.getWorldPosition(new THREE.Vector3());
  const c = new THREE.Vector3(...layout.wheel.centre);
  const left0 = hand('L'), right0 = hand('R');
  expect(left0.x).toBeGreaterThan(c.x + 0.1); expect(right0.x).toBeLessThan(c.x - 0.1);
  rig.pose(frame, layout, still, 90 * DEG);
  // A quarter turn left: the right hand is over the top, the left one at the bottom.
  expect(hand('R').y).toBeGreaterThan(hand('L').y + 0.2);
  rig.pose(frame, layout, still, 180 * DEG);
  // Half a turn: the hands have swapped sides below the centre (arms crossed), no re-grip.
  expect(hand('L').x).toBeLessThan(c.x); expect(hand('R').x).toBeGreaterThan(c.x);
});

test('steering wheel angle: 6.3 x the road-wheel angle for cars, capped at 200°; trucks 8 to 250°, buses 10 to 300°', () => {
  const car = fleetDriverLayout('golf7').wheel, van = fleetDriverLayout('euro-van').wheel, bus = fleetDriverLayout('transit-bus').wheel;
  expect(steeringAngle(car, 10 * DEG) / DEG).toBeCloseTo(63, 5);
  expect(steeringAngle(car, -40 * DEG) / DEG).toBeCloseTo(-200, 5);
  expect(steeringAngle(van, 40 * DEG) / DEG).toBeCloseTo(250, 5);
  expect(steeringAngle(bus, 20 * DEG) / DEG).toBeCloseTo(200, 5);
  expect(steeringAngle(bus, 40 * DEG) / DEG).toBeCloseTo(300, 5);
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

test('head looks into a turn and over the right shoulder when reversing', () => {
  const m = new DriverMotion();
  let p = m.pose;
  // Turning left at 30°/s with 10° of road-wheel angle.
  for (let t = 0, yaw = 0; t < 1.5; t += 1 / 60, yaw += 30 * DEG / 60) {
    p = m.update({ t, v: { x: 8 * Math.sin(yaw), y: 0, z: 8 * Math.cos(yaw) }, fwd: { x: Math.sin(yaw), y: 0, z: Math.cos(yaw) },
      left: { x: Math.cos(yaw), y: 0, z: -Math.sin(yaw) }, up: { x: 0, y: 1, z: 0 }, steer: 10 * DEG });
  }
  expect(p.headYaw / DEG).toBeCloseTo(0.12 * 30 + 0.8 * 10, 0);
  const r = new DriverMotion();
  const reversing = drive(r, () => -2, 1.2);
  expect(reversing.reverse).toBeGreaterThan(0.95);
  const stopped = drive(r, () => 0, 1.6, 1.2 + 1 / 60);
  expect(stopped.reverse).toBe(0);
});

test('a layout carries the class presets', () => {
  const classes = new Map(FLEET.map(m => [m.id, fleetDriverLayout(m.id).cls]));
  expect(classes.get('golf7')).toBe('hatch'); expect(classes.get('s90')).toBe('saloon'); expect(classes.get('prado-2010')).toBe('suv');
  expect(classes.get('euro-van')).toBe('van'); expect(classes.get('transit-bus')).toBe('bus'); expect(classes.get('garbage-truck')).toBe('truck');
  const l: DriverLayout = fleetDriverLayout('golf7');
  expect(l.wheel.grip / DEG).toBe(70);
});
