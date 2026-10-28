import { expect, test } from 'vitest';
import { startupDraftTiles, wantedTiles, leafParents } from '../../src/world/coarseRegion';

test('startup covers the collision radius of every start without waiting on far-LOD siblings', () => {
  const published = new Set(['t0_0_0', 't1_-1_-1']);
  const points = [{ x: 120, z: 120 }, { x: -260, z: -260 }];
  const required = startupDraftTiles(points, published, 50);
  expect(required).toEqual(['t0_-2_-2', 't0_-2_-1', 't0_-1_-2', 't0_-1_-1']);
  expect(required).not.toContain('t0_0_0');
  expect(startupDraftTiles([{ x: 120, z: 120 }], published, 200)).toContain('t0_1_0');
  const broad = wantedTiles(120, 120, published, leafParents(published));
  expect(broad.length).toBeGreaterThan(startupDraftTiles([points[0]], published, 50).length);
});
