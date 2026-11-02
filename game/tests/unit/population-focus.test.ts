import { expect, test } from 'vitest';
import { populationFocus } from '../../src/world/populationFocus';
import type { RoadGraphJson } from '../../src/gameplay/roadGraph';
const roads = { nodes: [0, 0, 100, 0], edges: [[0, 1, 3, 100]], edgeHeights: [[50, 50]] } as RoadGraphJson;
test('ground-relative aerial rings follow the visible street, without moving ordinary street rings', () => {
  const forward = { x: 1, y: -.5, z: 0 };
  expect(populationFocus({ x: 0, y: 52, z: 0 }, forward, [roads])).toEqual({ x: 0, z: 0 });
  expect(populationFocus({ x: 0, y: 150, z: 0 }, forward, [roads])).toEqual({ x: 160, z: 0 });
  expect(populationFocus({ x: 0, y: 300, z: 0 }, forward, [roads])).toEqual({ x: 300, z: 0 });
  expect(populationFocus({ x: 0, y: 300, z: 0 }, forward, [])).toEqual({ x: 0, z: 0 });
});
