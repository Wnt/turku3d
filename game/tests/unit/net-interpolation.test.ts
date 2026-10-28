import { expect, test } from 'vitest';
import { PoseBuffer } from '../../src/net/interpolation';
import type { NetPose } from '../../src/net/client';

const pose = (t: number, E: number, yaw = 0): NetPose => ({ t, E, N: 0, H: 3, yaw,
  vE: 10, vN: 0, vH: 0, mode: 'foot', vehicle: null, carrying: false });

test('network interpolation buffers 100 ms and extrapolates at most 200 ms', () => {
  const buffer = new PoseBuffer();
  buffer.push(pose(1, 0), 1000);
  buffer.push(pose(2, 1), 1100);
  expect(buffer.sample(1150)?.E).toBeCloseTo(0.5);
  expect(buffer.sample(1300)?.E).toBeCloseTo(2);
  expect(buffer.sample(9999)?.E).toBeCloseTo(3);
});

test('a rejoin timestamp epoch resets interpolation and yaw takes the shortest turn', () => {
  const buffer = new PoseBuffer();
  buffer.push(pose(100, 0, Math.PI - 0.1), 1000);
  buffer.push(pose(101, 1, -Math.PI + 0.1), 1100);
  expect(Math.abs(buffer.sample(1150)!.yaw)).toBeCloseTo(Math.PI);
  buffer.push(pose(1, 8), 1200);
  expect(buffer.sample(1250)?.E).toBe(8);
});

test('vehicle quaternion and wheels interpolate together without losing slope or taking the long arc', async () => {
  const { Quaternion, Euler } = await import('three');
  const { chassisRotation, vehicleRotation } = await import('../../src/net/orientation');
  const a = new Quaternion().setFromEuler(new Euler(0.3, Math.PI - 0.1, -0.2));
  const b = new Quaternion().setFromEuler(new Euler(0.4, Math.PI + 0.1, -0.3));
  const wheel = (y: number, steer: number, rotation: number) => ({ pos: { x: 1, y, z: 1 }, steer, rotation, contact: true, suspension: 0.2 });
  const buffer = new PoseBuffer();
  buffer.push({ ...pose(1, 0), mode: 'car', vehicle: 'player', rot: a, wheels: [wheel(-0.2, 0, 6)] }, 1000);
  buffer.push({ ...pose(2, 1), mode: 'car', vehicle: 'player', rot: b, wheels: [wheel(-0.4, 0.4, 8)] }, 1100);
  const mid = buffer.sample(1150)!;
  expect(chassisRotation(mid).angleTo(a.clone().slerp(b, 0.5))).toBeLessThan(1e-7);
  expect(mid.wheels![0].pos.y).toBeCloseTo(-0.3);
  expect(mid.wheels![0].steer).toBeCloseTo(0.2);
  expect(mid.wheels![0].rotation).toBeCloseTo(7);
  expect(vehicleRotation(mid, true).angleTo(chassisRotation(mid))).toBeCloseTo(Math.PI / 2);
  expect(chassisRotation(buffer.sample(9999)!).angleTo(b)).toBeLessThan(1e-7);
  const antipodal = { ...b, x: -b.x, y: -b.y, z: -b.z, w: -b.w };
  buffer.push({ ...pose(3, 2), mode: 'car', vehicle: 'player', rot: antipodal }, 1200);
  expect(chassisRotation(buffer.sample(1250)!).angleTo(b)).toBeLessThan(1e-7);
});

test('legacy yaw converts to the chassis frame and model switches do not blend vehicles', async () => {
  const { Vector3 } = await import('three');
  const { chassisRotation } = await import('../../src/net/orientation');
  const forward = new Vector3(1, 0, 0).applyQuaternion(chassisRotation({ yaw: 0 }));
  expect(forward.z).toBeCloseTo(-1);
  const buffer = new PoseBuffer();
  buffer.push(pose(1, 0), 1000);
  buffer.push({ ...pose(2, 10), mode: 'car', vehicle: 'player' }, 1100);
  expect(buffer.sample(1150)!.E).toBe(10);
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
