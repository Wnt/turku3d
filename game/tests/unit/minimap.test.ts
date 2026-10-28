import { expect, test, vi } from 'vitest';
import { REROUTE_M, RouteTracker, compassDeg, formatMoney, formatTimer, project } from '../../src/gameplay/minimap';
import type { RouteTarget, Router, WorldPoint } from '../../src/gameplay/contract';

test('route progresses without replanning and recomputes for target or mode changes', () => {
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
  expect(calls).toHaveLength(3);
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


test('sustained off-route deviation replans; temporary street jitter does not', () => {
  const clock = vi.spyOn(performance, 'now');
  clock.mockReturnValue(0);
  const route = vi.fn((from: WorldPoint, to: WorldPoint) => [from, to]);
  const tracker = new RouteTracker({ route, distance: () => 100 });
  const target: RouteTarget = { id: 'far', point: { x: 1000, z: 0 }, label: 'Far', kind: 'dropoff' };
  tracker.update({ x: 0, z: 0 }, target, 'car');
  clock.mockReturnValue(3000);
  tracker.update({ x: 260, z: 5 }, target, 'car');
  tracker.invalidate(); // a tile arriving must not replace a valid route
  expect(tracker.route?.[0].x).toBe(260);
  expect(route).toHaveBeenCalledTimes(1);
  tracker.update({ x: 280, z: 40 }, target, 'car');
  clock.mockReturnValue(3500);
  tracker.update({ x: 285, z: 10 }, target, 'car');
  tracker.update({ x: 290, z: 40 }, target, 'car');
  clock.mockReturnValue(4600);
  tracker.update({ x: 300, z: 45 }, target, 'car');
  expect(route).toHaveBeenCalledTimes(2);
  clock.mockRestore();
});

test('expanded map fits the complete distant route and the player', async () => {
  const { routeView } = await import('../../src/gameplay/routeTracker');
  const fit = routeView({ x: 0, z: 0 }, [{ x: -2000, z: 500 }, { x: 3000, z: -1000 }], 650);
  expect(fit.centre).toEqual({ x: 500, z: -250 });
  expect(fit.span).toBe(6000);
});


test('a newly available graph retries a missing route while stationary', () => {
  const route = vi.fn().mockReturnValueOnce(null).mockReturnValue([{ x: 0, z: 0 }, { x: 3000, z: 0 }]);
  const tracker = new RouteTracker({ route, distance: () => 3000 });
  const target: RouteTarget = { id: 'unloaded', point: { x: 3000, z: 0 }, label: 'Unloaded', kind: 'dropoff' };
  tracker.update({ x: 0, z: 0 }, target, 'car');
  expect(tracker.route).toBeNull();
  tracker.invalidate();
  expect(tracker.update({ x: 0, z: 0 }, target, 'car')).toBe(true);
  expect(tracker.route).toHaveLength(2);
});


test('nearby return streets do not make progress jump over an untravelled loop', () => {
  const points = [{ x: 0, z: 0 }, { x: 100, z: 0 }, { x: 100, z: 20 }, { x: 0, z: 20 }];
  const distance = (path: WorldPoint[]) => path.slice(1).reduce((sum, p, i) => sum + Math.hypot(p.x - path[i].x, p.z - path[i].z), 0);
  const route = vi.fn(() => points);
  const tracker = new RouteTracker({ route, distance });
  const target: RouteTarget = { id: 'return', point: points.at(-1)!, label: 'Return', kind: 'dropoff' };
  tracker.update(points[0], target, 'car');
  tracker.update({ x: 6, z: 12 }, target, 'car');
  expect(tracker.route?.[0]).toEqual({ x: 6, z: 0 });
  expect(tracker.length).toBe(214);
  expect(route).toHaveBeenCalledTimes(1);
});
