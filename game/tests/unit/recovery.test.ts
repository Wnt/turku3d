import { describe, expect, test } from 'vitest';
import { PlayableArea, RECOVERY, RecoveryMonitor, lostReason, lowestGround, type Probe } from '../../src/gameplay/recovery';

// An L-shaped area of three 62.5 m tiles: x 0..125 on row 0, plus x 0..62.5 on row 1.
const area = new PlayableArea(['c_0_0', 'c_1_0', 'c_0_1', 'not-a-tile']);
const spawn = { time: 0, x: 30, y: 2, z: 30, heading: 0, inVehicle: false };
const probe = (time: number, x: number, over: Partial<Probe> = {}): Probe => ({ time, x, y: 2, z: 30, heading: 0.5,
  inVehicle: false, grounded: true, loaded: true, vy: 0, steady: true, waterDepth: null, ...over });

describe('playable area', () => {
  test('is the tile union with its bounding box', () => {
    expect(area.bbox).toEqual({ x0: 0, z0: 0, x1: 125, z1: 125 });
    expect(area.has(100, 30)).toBe(true);
    expect(area.has(100, 100)).toBe(false); // the missing corner of the L
    expect(area.outsideBy(130, 30)).toBeCloseTo(5);
    expect(area.outsideBy(100, 100)).toBe(0);
  });
  test('edge distance covers the outer edge and the inner corner, capped at the warning distance', () => {
    expect(area.edgeDistance(110, 30)).toBeCloseTo(15); // east edge at x 125
    expect(area.edgeDistance(30, 30)).toBe(Infinity);
    expect(area.edgeDistance(100, 55)).toBeCloseTo(7.5); // the missing tile c_1_1 starts at z 62.5
    expect(area.edgeDistance(140, 30)).toBe(0);
  });
  test('inset moves a point 2 m inside every edge, corners included', () => {
    expect(area.inset({ x: 124.5, z: 30 }, 2)).toEqual({ x: 123, z: 30 });
    const corner = area.inset({ x: 124, z: 1 }, 2);
    expect(corner.x).toBeCloseTo(123); expect(corner.z).toBeCloseTo(2);
    expect(area.inset({ x: 50, z: 50 }, 2)).toEqual({ x: 50, z: 50 });
  });
  test('lowest ground of the loaded tiles', () => {
    expect(lowestGround(['c_0_0', 'c_1_0'], { c_0_0: { h: [2, 9] }, c_1_0: { h: [-1.5, 4] } })).toBe(-1.5);
    expect(lowestGround([], {})).toBe(Infinity);
  });
});

describe('lost rules', () => {
  test('below ground, outside the area, under water for 2 s, airborne for 3 s', () => {
    expect(lostReason(probe(10, 30, { y: -3.1 }), 2, area, null, null)).toBe('below');
    expect(lostReason(probe(10, 30, { y: -2.9 }), 2, area, null, null)).toBe(null);
    expect(lostReason(probe(10, 131), 2, area, null, null)).toBe('outside');
    expect(lostReason(probe(10, 129), 2, area, null, null)).toBe(null);
    expect(lostReason(probe(10, 30), 2, area, 7.9, null)).toBe('water');
    expect(lostReason(probe(10, 30), 2, area, 8.1, null)).toBe(null);
    expect(lostReason(probe(10, 30), 2, area, null, 6.9)).toBe('airborne');
    expect(lostReason(probe(10, 30), 2, area, null, 7.1)).toBe(null);
  });

  test('a car driven off the east edge resets at the sample 3 s before it left the ground, inside the edge', () => {
    const m = new RecoveryMonitor(area, spawn);
    let t = 0, x = 0;
    for (; x < 125; t += 0.1, x += 1) expect(m.update(probe(t, x, { inVehicle: true }), 2)).toBe(false); // 10 m/s
    expect(m.samples.length).toBeGreaterThanOrEqual(RECOVERY.keepSeconds);
    expect(m.samples.length).toBeLessThanOrEqual(RECOVERY.keepSeconds + 1);
    const leftAt = t - 0.1;
    // Off the edge: no ground contact, falling.
    for (let y = 2; !m.lost; t += 0.1, y -= 1) m.update(probe(t, x, { inVehicle: true, grounded: false, vy: -10, y }), 2);
    expect(m.lost?.reason).toBe('below');
    const target = m.target();
    expect(target.time).toBeLessThanOrEqual(leftAt - RECOVERY.lookBack);
    expect(target.time).toBeGreaterThan(leftAt - RECOVERY.lookBack - RECOVERY.sampleEvery - 1e-9);
    expect(target.x).toBeLessThanOrEqual(125 - RECOVERY.edgeMargin - RECOVERY.carMargin);
    expect(target.inVehicle).toBe(true);
    // Countdown: an automatic reset after 5 s.
    const since = m.lost!.since;
    expect(m.update(probe(since + 4.9, x, { grounded: false, y: -50 }), 2)).toBe(false);
    expect(m.countdown(since + 4.9)).toBeCloseTo(0.1);
    expect(m.update(probe(since + 5, x, { grounded: false, y: -50 }), 2)).toBe(true);
    m.recovered(target, since + 5);
    expect(m.lost).toBe(null);
    expect(m.samples.every(s => s.time <= target.time)).toBe(true);
    expect(m.resets).toBe(1);
    // Stale state from before the reset is ignored for the grace period.
    expect(m.update(probe(since + 5.2, x, { grounded: false, y: -60 }), 2)).toBe(false);
    expect(m.lost).toBe(null);
  });

  test('walking into the river: lost after 2 s under the surface, reset before the bank', () => {
    const m = new RecoveryMonitor(area, spawn);
    let t = 0;
    for (; t < 6; t += 0.1) m.update(probe(t, 20 + t * 3.6), 2);
    const dry = t - 0.1;
    // Down the bank: grounded on the river bed, 1.5 m under the water surface.
    for (; !m.lost; t += 0.1) m.update(probe(t, 20 + t * 3.6, { y: 0, waterDepth: 1.5 }), 2);
    expect(m.lost?.reason).toBe('water');
    expect(t - dry).toBeGreaterThan(RECOVERY.waterSeconds);
    expect(t - dry).toBeLessThan(RECOVERY.waterSeconds + 0.5);
    expect(m.target().time).toBeLessThanOrEqual(dry - RECOVERY.lookBack);
  });

  test('a short fall that lands back on solid ground cancels the prompt', () => {
    const m = new RecoveryMonitor(area, spawn);
    for (let t = 0; t < 2; t += 0.1) m.update(probe(t, 30), 2);
    for (let t = 2; t < 5.5; t += 0.1) m.update(probe(t, 30, { grounded: false, y: 40 - t }), 2);
    expect(m.lost?.reason).toBe('airborne');
    m.update(probe(5.6, 30), 2);
    expect(m.lost).toBe(null);
  });

  test('unsafe frames record no samples; without any sample the spawn is the target', () => {
    const m = new RecoveryMonitor(area, spawn);
    m.update(probe(0, 30, { loaded: false }), 2);
    m.update(probe(1, 30, { vy: -3 }), 2);
    m.update(probe(2, 30, { waterDepth: 0.1 }), 2);
    m.update(probe(3, 30, { steady: false, inVehicle: true }), 2);
    m.update(probe(4, 100, { z: 100 }), 2); // the missing corner tile
    expect(m.samples).toEqual([]);
    expect(m.target()).toMatchObject({ x: spawn.x, z: spawn.z });
    m.update(probe(5, 40, { waterDepth: -3 }), 2); // on a bridge above the river
    expect(m.samples.map(s => s.x)).toEqual([40]);
  });
});
