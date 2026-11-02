import { expect, test } from 'vitest';
import { FootPlant } from '../../src/actors/footPlant';

test('a planted foot stays on its contact while the animated trajectory drifts', () => {
  const foot = new FootPlant();
  foot.update(1 / 60, { x: 2, y: 0.1, z: 5 }, 0, true);
  for (let i = 1; i <= 10; i++) {
    const target = foot.update(1 / 60, { x: 2, y: 0.1, z: 5 + i * 0.01 }, 0.01, true);
    expect(target.z).toBeCloseTo(5);
  }
  const release = foot.update(1 / 60, { x: 2, y: 0.2, z: 5.1 }, 0.1, true);
  expect(release.z).toBeGreaterThan(5); expect(release.z).toBeLessThan(5.1);
  for (let i = 0; i < 12; i++) foot.update(1 / 60, { x: 2, y: 0.2, z: 5.2 }, 0.1, true);
  expect(foot.update(1 / 60, { x: 2, y: 0.1, z: 5.2 }, 0, true).z).toBeCloseTo(5.2);
});

test('airborne, lost contact and teleports cannot drag a foot from its former stance', () => {
  const foot = new FootPlant();
  foot.update(1 / 60, { x: 0, y: 0, z: 0 }, 0, true);
  expect(foot.update(1 / 60, { x: 1, y: 2, z: 0 }, 0.1, false)).toEqual({ x: 1, z: 0, weight: 0 });
  foot.update(1 / 60, { x: 1, y: 0, z: 0 }, 0, true);
  expect(foot.update(1 / 60, { x: 100, y: 0, z: 0 }, 0, true).x).toBe(100);
  foot.reset();
  expect(foot.update(1 / 60, { x: 102, y: 0.2, z: 0 }, 0.2, true).weight).toBe(0);
});
