import { expect, test } from 'vitest';
import { startupDraftTiles, wantedTiles } from '../../src/world/coarseRegion';

test('startup covers every selectable start, including the old published starting area', () => {
  const points = [{ x: 120, z: 120 }, { x: -260, z: -260 }];
  const required = startupDraftTiles(points, 50);
  expect(required).toEqual(['t0_0_0', 't0_-2_-2', 't0_-2_-1', 't0_-1_-2', 't0_-1_-1']);
  expect(startupDraftTiles([points[0]], 200)).toContain('t0_1_0');
  expect(wantedTiles(120, 120)).toHaveLength(9);
});
