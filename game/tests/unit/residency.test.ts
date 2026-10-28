import { expect, test } from 'vitest';
import { distToVisualTile, refinableParents, ringParents, RESIDENT_L0 } from '../../src/world/residency';

const quadrants = (i: number, j: number) =>
  [0, 1].flatMap((dj) => [0, 1].map((di) => `t0_${2 * i + di}_${2 * j + dj}`));

test('distance to a visual tile footprint uses its level size', () => {
  expect(distToVisualTile('t0_2_0', 600, 100)).toBe(0);
  expect(distToVisualTile('t0_2_0', 400, 100)).toBe(100);
  expect(distToVisualTile('t1_1_0', 1100, 100)).toBe(100);
  expect(distToVisualTile('c_1_1', 0, 0)).toBe(Infinity);
});

test('the office block and a fifth tile east of it all refine at the office', () => {
  const parents = new Map([['t1_0_0', quadrants(0, 0)], ['t1_1_0', quadrants(1, 0)]]);
  expect([...refinableParents(parents, 168, 246)].sort()).toEqual(['t1_0_0', 't1_1_0']);
});

test('a large tile list keeps at most nine L0 tiles refined, nearest parents first', () => {
  const parents = new Map<string, string[]>();
  for (let i = -3; i <= 3; i++) for (let j = -3; j <= 3; j++) parents.set(`t1_${i}_${j}`, quadrants(i, j));
  for (const [x, z] of [[168, 246], [625, 125], [-1400, 1700], [5000, -5000]]) {
    const refined = refinableParents(parents, x, z);
    const resident = [...refined].reduce((n, id) => n + parents.get(id)!.length, 0);
    expect(resident).toBeLessThanOrEqual(RESIDENT_L0);
    const nearest = [...parents.keys()].sort((a, b) => distToVisualTile(a, x, z) - distToVisualTile(b, x, z))[0];
    expect(refined.has(nearest)).toBe(true);
  }
});

test('partially covered parents still fill the remaining budget', () => {
  const parents = new Map([['t1_0_0', quadrants(0, 0)], ['t1_1_0', ['t0_2_0']], ['t1_0_1', quadrants(0, 1)],
    ['t1_-1_0', quadrants(-1, 0)]]);
  // Ranked t1_0_0 (4), t1_1_0 (1), t1_-1_0 (4) = 9; t1_0_1 would exceed the cap.
  expect([...refinableParents(parents, 300, 100)].sort()).toEqual(['t1_-1_0', 't1_0_0', 't1_1_0']);
  expect([...refinableParents(parents, 300, 100, 8)].sort()).toEqual(['t1_0_0', 't1_1_0']);
  expect(refinableParents(parents, 300, 100, 13).size).toBe(4);
});

test('the 360-degree ring includes every intersecting child and its REPLACE siblings without a count cap', () => {
  const parents = new Map<string, string[]>();
  for (let i = -2; i <= 2; i++) for (let j = -2; j <= 2; j++) parents.set(`t1_${i}_${j}`, quadrants(i, j));
  const selected = ringParents(parents, 0, 0);
  expect(selected.size * 4).toBeGreaterThan(RESIDENT_L0);
  for (const [id, children] of parents) {
    if (children.some(child => distToVisualTile(child, 0, 0) <= 250)) expect(selected.has(id)).toBe(true);
  }
  expect([...selected].slice(0, 4).sort()).toEqual(['t1_-1_-1', 't1_-1_0', 't1_0_-1', 't1_0_0']);
});

test('ring eviction has hysteresis, but a new far parent cannot enter through that margin', () => {
  const parents = new Map([['t1_0_0', quadrants(0, 0)]]);
  const selected = ringParents(parents, 249, 200);
  expect([...ringParents(parents, 800, 200, selected)]).toEqual(['t1_0_0']);
  expect([...ringParents(parents, 800, 200)]).toEqual([]);
  expect([...ringParents(parents, 876, 200, selected)]).toEqual([]);
});
