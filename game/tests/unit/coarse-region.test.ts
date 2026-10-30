import { expect, test } from 'vitest';
import { startupDraftTiles, wantedTiles } from '../../src/world/coarseRegion';

test('mobile detail covers both sides of tile seams without loading the entire desktop ring', () => {
  expect(wantedTiles(125, 125, 80)).toEqual(['t0_0_0']);
  expect(new Set(wantedTiles(249, 249, 80))).toEqual(new Set(['t0_0_0', 't0_0_1', 't0_1_0', 't0_1_1']));
  expect(wantedTiles(171, 253, 80)).not.toContain('t0_-1_0');
  expect(wantedTiles(171, 253)).toHaveLength(9);
});

test('startup covers every selectable start, including the old published starting area', () => {
  const points = [{ x: 120, z: 120 }, { x: -260, z: -260 }];
  const required = startupDraftTiles(points, 50);
  expect(required).toEqual(['t0_0_0', 't0_-2_-2', 't0_-2_-1', 't0_-1_-2', 't0_-1_-1']);
  expect(startupDraftTiles([points[0]], 200)).toContain('t0_1_0');
  expect(wantedTiles(120, 120)).toHaveLength(9);
});

test('a predicted crossing precedes a closer visible sibling and turn replaces the old cone', async () => {
  const { aheadTiles, draftDemand } = await import('../../src/world/coarseRegion');
  const visible = wantedTiles(490, 125);
  const east = draftDemand(visible, aheadTiles(490, 125, 14, 0), 490, 125, 14, 0);
  expect(east.urgent.slice(0, 2)).toEqual(['t0_1_0', 't0_2_0']);
  expect(east.tiles.indexOf('t0_2_0')).toBeLessThan(east.tiles.indexOf('t0_1_1'));
  const west = draftDemand(visible, aheadTiles(490, 125, -14, 0), 490, 125, -14, 0);
  expect(west.urgent).toContain('t0_0_0');
  expect(west.urgent).not.toContain('t0_2_0');
  expect(draftDemand(visible, [], 490, 125, 0, 0).urgent).toEqual(['t0_1_0']);
});

test('route prediction follows a bend within the travel horizon and ignores off-route hints', async () => {
  const { routeAheadTiles, draftDemand } = await import('../../src/world/coarseRegion');
  const points = [{ x: 125, z: 125 }, { x: 375, z: 125 }, { x: 375, z: 2125 }];
  const route = routeAheadTiles(points, 125, 125, 14)!;
  expect(route.urgent).toEqual(['t0_0_0', 't0_1_0', 't0_1_1', 't0_1_2']);
  expect(route.tiles).not.toContain('t0_1_8');
  const d = draftDemand(wantedTiles(125, 125), route.tiles, 125, 125, 14, 0, route.urgent);
  expect(d.urgent).toEqual(route.urgent);
  expect(d.tiles.indexOf('t0_1_1')).toBeLessThan(d.tiles.indexOf('t0_0_1'));
  expect(routeAheadTiles(points, -500, -500, 14)).toBeNull();
  expect(routeAheadTiles(points, 125, 125, 0)).toBeNull();
});


test('motion prediction gives both passes a minute of lead time while bounding fast flight', async () => {
  const { aheadTiles } = await import('../../src/world/coarseRegion');
  expect(aheadTiles(125, 125, 50 / 3.6, 0)).toContain('t0_4_0');
  const flight = aheadTiles(125, 125, 500, 0);
  expect(flight).toContain('t0_12_0');
  expect(flight).not.toContain('t0_14_0');
  expect(flight.length).toBeLessThan(60);
});
