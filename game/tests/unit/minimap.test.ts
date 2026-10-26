import { expect, test } from 'vitest';
import { REROUTE_M, RouteTracker, compassDeg, formatMoney, formatTimer, project } from '../../src/gameplay/minimap';
import type { RouteTarget, Router, WorldPoint } from '../../src/gameplay/contract';

test('route is recomputed on a new target, a mode change or after moving more than 5 m', () => {
  const calls: WorldPoint[] = [];
  const router: Router = {
    route: (from, to) => { calls.push(from); return [from, to]; },
    distance: path => Math.hypot(path[1].x - path[0].x, path[1].z - path[0].z),
  };
  const tracker = new RouteTracker(router);
  const target: RouteTarget = { id: 'pizzeria', point: { x: 30, z: 40 }, label: 'Pizzeria', kind: 'pickup' };
  expect(tracker.update({ x: 0, z: 0 }, target, 'foot')).toBe(true);
  expect(tracker.length).toBe(50);
  expect(tracker.update({ x: REROUTE_M - 0.1, z: 0 }, target, 'foot')).toBe(false);
  expect(tracker.update({ x: REROUTE_M + 0.1, z: 0 }, target, 'foot')).toBe(true);
  expect(tracker.update({ x: REROUTE_M + 0.1, z: 0 }, target, 'car')).toBe(true);
  expect(tracker.update({ x: REROUTE_M + 0.1, z: 0 }, { ...target, point: { x: 31, z: 40 } }, 'car')).toBe(true);
  expect(calls).toHaveLength(4);
  expect(tracker.update({ x: 0, z: 0 }, null, 'car')).toBe(true);
  expect(tracker.route).toBeNull();
  expect(tracker.length).toBe(0);
  expect(tracker.update({ x: 50, z: 0 }, null, 'car')).toBe(false);
});

test('heading-up projection puts the facing direction at the top of the map', () => {
  const centre = { x: 100, z: 100 };
  // North (-z) is up when facing north.
  expect(project({ x: 100, z: 90 }, centre, 0, 2).map(v => Math.round(v))).toEqual([0, -20]);
  // Facing east (heading 90): a point 10 m east is straight up.
  const [x, y] = project({ x: 110, z: 100 }, centre, -Math.PI / 2, 1);
  expect(x).toBeCloseTo(0);
  expect(y).toBeCloseTo(-10);
  expect(compassDeg(1, 0)).toBeCloseTo(90);
  expect(compassDeg(0, -1)).toBeCloseTo(0);
  expect(compassDeg(0, 1)).toBeCloseTo(180);
  expect(compassDeg(-1, 0)).toBeCloseTo(270);
});

test('timer and money formatting', () => {
  expect(formatTimer(75.2)).toBe('1:16');
  expect(formatTimer(9)).toBe('0:09');
  expect(formatTimer(-3)).toBe('0:00');
  expect(formatMoney(1250.4)).toBe('€1,250');
});
