import { expect, test } from 'vitest';
import { collisionCovers } from '../../src/physics/collisionCoverage';

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
