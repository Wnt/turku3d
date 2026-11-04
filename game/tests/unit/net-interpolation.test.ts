import { expect, test } from 'vitest';
import { PoseBuffer } from '../../src/net/interpolation';
import type { NetPose } from '../../src/net/client';

const pose = (t: number, E: number, yaw = 0): NetPose => ({ t, E, N: 0, H: 3, yaw,
  vE: 10, vN: 0, vH: 0, mode: 'foot', vehicle: null, carrying: false });

/** Two poses 100 ms apart delivered on time: drawn one spacing plus the 30 ms minimum behind. */
const DELAY = 130;

test('network poses at 55 ms with jittered delivery move every 60 fps frame at their speed', () => {
  let seed = 3;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const buffer = new PoseBuffer();
  const sent = Array.from({ length: 60 }, (_, k) => ({ t: 1000 + 55 * k, arrival: 1000 + 55 * k + 40 + 30 * rnd() ** 2 }));
  let next = 0, last = NaN;
  const steps: number[] = [];
  for (let now = 1000; now < 1000 + 55 * 60; now += 1000 / 60) {
    while (next < sent.length && sent[next].arrival <= now) { const s = sent[next++]; buffer.push(pose(s.t, (s.t - 1000) / 100), now); }
    const p = buffer.sample(now);
    if (p && !Number.isNaN(last) && now > 1500) steps.push((p.E - last) / (1 / 60));
    if (p) last = p.E;
  }
  for (const v of steps) { expect(v).toBeGreaterThan(9); expect(v).toBeLessThan(11); }
  // After the last pose the remote carries on along its velocity for at most 250 ms, then stops.
  const end = sent.at(-1)!;
  expect(buffer.sample(end.arrival + 10000)!.E).toBeCloseTo((end.t - 1000) / 100 + 10 * 0.25, 1);
});

test('a rejoin timestamp epoch resets interpolation and yaw takes the shortest turn', () => {
  const buffer = new PoseBuffer();
  buffer.push(pose(1000, 0, Math.PI - 0.1), 1000);
  buffer.push(pose(1100, 1, -Math.PI + 0.1), 1100);
  expect(Math.abs(buffer.sample(1050 + DELAY)!.yaw)).toBeCloseTo(Math.PI);
  buffer.push(pose(1, 8), 1300);
  expect(buffer.sample(1300)?.E).toBe(8);
});

test('vehicle quaternion and wheels interpolate together without losing slope or taking the long arc', async () => {
  const { Quaternion, Euler } = await import('three');
  const { chassisRotation, vehicleRotation } = await import('../../src/net/orientation');
  const a = new Quaternion().setFromEuler(new Euler(0.3, Math.PI - 0.1, -0.2));
  const b = new Quaternion().setFromEuler(new Euler(0.4, Math.PI + 0.1, -0.3));
  const wheel = (y: number, steer: number, rotation: number) => ({ pos: { x: 1, y, z: 1 }, steer, rotation, contact: true, suspension: 0.2 });
  const buffer = new PoseBuffer();
  buffer.push({ ...pose(1000, 0), mode: 'car', vehicle: 'player', rot: a, wheels: [wheel(-0.2, 0, 6)] }, 1000);
  buffer.push({ ...pose(1100, 1), mode: 'car', vehicle: 'player', rot: b, wheels: [wheel(-0.4, 0.4, 8)] }, 1100);
  const mid = buffer.sample(1050 + DELAY)!;
  expect(chassisRotation(mid).angleTo(a.clone().slerp(b, 0.5))).toBeLessThan(1e-7);
  expect(mid.wheels![0].pos.y).toBeCloseTo(-0.3);
  expect(mid.wheels![0].steer).toBeCloseTo(0.2);
  expect(mid.wheels![0].rotation).toBeCloseTo(7);
  expect(vehicleRotation(mid, true).angleTo(chassisRotation(mid))).toBeCloseTo(Math.PI / 2);
  expect(chassisRotation(buffer.sample(1100 + DELAY)!).angleTo(b)).toBeLessThan(1e-6);
  const antipodal = { ...b, x: -b.x, y: -b.y, z: -b.z, w: -b.w };
  buffer.push({ ...pose(1200, 2), mode: 'car', vehicle: 'player', rot: antipodal }, 1200);
  expect(chassisRotation(buffer.sample(1150 + DELAY)!).angleTo(b)).toBeLessThan(1e-6);
});

test('legacy yaw converts to the chassis frame and model switches do not blend vehicles', async () => {
  const { Vector3 } = await import('three');
  const { chassisRotation } = await import('../../src/net/orientation');
  const forward = new Vector3(1, 0, 0).applyQuaternion(chassisRotation({ yaw: 0 }));
  expect(forward.z).toBeCloseTo(-1);
  const buffer = new PoseBuffer();
  buffer.push(pose(1000, 0), 1000);
  buffer.push({ ...pose(1100, 10), mode: 'car', vehicle: 'player' }, 1100);
  expect(buffer.sample(1150 + DELAY)!.E).toBe(10);
});

test('parked +Z and starter +X forward axes agree for arbitrary heading, pitch and roll', async () => {
  const { Quaternion, Euler, Vector3 } = await import('three');
  const { vehicleRotation } = await import('../../src/net/orientation');
  for (const heading of [0, Math.PI / 2, -Math.PI / 2, Math.PI]) {
    const rot = new Quaternion().setFromEuler(new Euler(0.2, heading, -0.3));
    const starter = new Vector3(1, 0, 0).applyQuaternion(vehicleRotation({ yaw: 0, rot }, false));
    const parked = new Vector3(0, 0, 1).applyQuaternion(vehicleRotation({ yaw: 0, rot }, true));
    expect(starter.distanceTo(parked)).toBeLessThan(1e-7);
    expect(starter.distanceTo(new Vector3(1, 0, 0).applyQuaternion(rot))).toBeLessThan(1e-7);
  }
});
