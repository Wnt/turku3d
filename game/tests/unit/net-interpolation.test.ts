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
