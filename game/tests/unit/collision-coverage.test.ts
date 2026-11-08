import { expect, test } from 'vitest';
import { bodyReach, boxTouches, collisionCovers, coverageReach, easedSpeed, easeInput } from '../../src/physics/collisionCoverage';

test('stationary actors require actual resident collision, including outside the manifest', () => {
  const loaded = new Set(['c_0_0']);
  expect(collisionCovers(loaded, 20, 20)).toBe(true);
  expect(collisionCovers(loaded, 80, 20)).toBe(false);
  loaded.delete('c_0_0');
  expect(collisionCovers(loaded, 20, 20)).toBe(false);
  loaded.add('c_0_0');
  expect(collisionCovers(loaded, 20, 20)).toBe(true);
});

test('checks the full swept footprint before a fast or diagonal crossing', () => {
  const loaded = new Set(['c_0_0', 'c_1_0', 'c_2_0']);
  expect(collisionCovers(loaded, 30, 30, 180, 30, 2)).toBe(true);
  loaded.delete('c_1_0');
  expect(collisionCovers(loaded, 30, 30, 180, 30, 2)).toBe(false);
  expect(collisionCovers(loaded, 61, 61, 63, 63, 0.32)).toBe(false);
});

test('a capsule or car overlapping a cell boundary waits for both sides', () => {
  const loaded = new Set(['c_0_0']);
  expect(collisionCovers(loaded, 62.3, 20, 62.3, 20, 0.32)).toBe(false);
  loaded.add('c_1_0');
  expect(collisionCovers(loaded, 62.3, 20, 62.3, 20, 0.32)).toBe(true);
  expect(collisionCovers(loaded, 61, 20, 61, 20, 3)).toBe(true);
});

test('negative coordinates and exact corner contact need all touched cells', () => {
  const loaded = new Set(['c_-1_-1']);
  expect(collisionCovers(loaded, -30, -30, -20, -20, 2)).toBe(true);
  expect(collisionCovers(loaded, -1, -1, -1, -1, 1)).toBe(false);
  for (const id of ['c_0_-1', 'c_-1_0', 'c_0_0']) loaded.add(id);
  expect(collisionCovers(loaded, -1, -1, -1, -1, 1)).toBe(true);
});

test('coverage includes the next step before an actor crosses a loaded edge', () => {
  const loaded = new Set(['c_0_0']);
  expect(collisionCovers(loaded, 59, 30, 59, 30, 3)).toBe(true);
  expect(collisionCovers(loaded, 59, 30, 60, 30, 3)).toBe(false);
});

test('coverage reach finds the gap ahead to half a metre, and the eased input brakes only above the eased speed', () => {
  const loaded = new Set(['c_0_0', 'c_1_0', 'c_2_0']);
  // A 3 m footprint going east from x 10 touches c_3_0 once its centre passes 184.5 m.
  const reach = coverageReach(loaded, 10, 30, 1, 0, 3, 400);
  expect(reach).toBeGreaterThan(184 - 10 - 0.5); expect(reach).toBeLessThanOrEqual(184.5 - 10);
  expect(coverageReach(loaded, 10, 30, -1, 0, 3, 400)).toBe(7);
  expect(coverageReach(loaded, 10, 30, 1, 0, 3, 100)).toBe(Infinity);
  const allowed = easedSpeed(42); // 5 m/s² over 40 m: 20 m/s
  expect(allowed).toBeCloseTo(20);
  const input = { throttle: 1, steer: 0.3, brake: 0, handbrake: false };
  expect(easeInput(input, 10, 10, allowed)).toBe(input);
  expect(easeInput(input, 19, 19, allowed)).toEqual({ ...input, throttle: 0, brake: 0 });
  const braking = easeInput(input, 24, 24, allowed);
  expect(braking.throttle).toBe(0); expect(braking.brake).toBeGreaterThan(0.9); expect(braking.steer).toBe(0.3);
  // Rolling backwards towards the gap: forward drive is the brake (vehicle/handling.ts).
  const back = easeInput({ ...input, throttle: 0, brake: 1 }, -24, 24, allowed);
  expect(back.brake).toBe(0); expect(back.throttle).toBeGreaterThan(0.9);
});

test('a chassis box meets a walled cell only where its body would: alongside it, never; heading in, short of it', () => {
  const walled = new Set(['c_3_0', 'c_0_-1', 'c_1_-1', 'c_2_-1', 'c_3_-1']);
  // Driving east 2.5 m from the walled row below z = 0: a 2.8 m circle touches it at once, a 2.6 x 1.4 m box never does.
  const east = { ux: 1, uz: 0, a: 2.6, b: 1.4 };
  expect(collisionCovers({ has: (c: string) => !walled.has(c) }, 10, 2.5, 10, 2.5, 2.8)).toBe(false);
  expect(boxTouches(walled, 10, 2.5, east)).toBe(false);
  expect(boxTouches(walled, 10, 1.3, east)).toBe(true);
  // Heading east into c_3_0 (x from 187.5): the box's nose gets there once its centre passes 184.9 m.
  const reach = bodyReach(walled, 10, 30, 1, 0, east, 400);
  expect(reach).toBeGreaterThan(184.9 - 10 - 0.5); expect(reach).toBeLessThanOrEqual(184.9 - 10);
  expect(bodyReach(walled, 10, 2.5, 1, 0, east, 150)).toBe(Infinity);
  // Turned 45 degrees at the same spot: its corner reaches 2.8 m down and into the walled row.
  const diagonal = { ux: Math.SQRT1_2, uz: Math.SQRT1_2, a: 2.6, b: 1.4 };
  expect(boxTouches(walled, 10, 2.5, diagonal)).toBe(true);
  expect(boxTouches(walled, 10, 3, diagonal)).toBe(false);
});
