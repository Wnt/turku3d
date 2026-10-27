import { describe, expect, test } from 'vitest';
import { PlayableArea, RECOVERY, RecoveryMonitor, lostReason, lowestGround, type Probe } from '../../src/gameplay/recovery';

// An L-shaped area of three 62.5 m tiles: x 0..125 on row 0, plus x 0..62.5 on row 1.
const area = new PlayableArea({ c_0_0: {}, c_1_0: {}, c_0_1: {}, 'not-a-tile': {} });
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
  test('tiles added at runtime join the area and grow its bounding box once reached', () => {
    const collision: Record<string, object> = { c_0_0: {} };
    const growing = new PlayableArea(collision);
    expect(growing.edgeDistance(55, 30)).toBeCloseTo(7.5);
    collision.c_1_0 = {};
    expect(growing.edgeDistance(55, 30)).toBe(Infinity);
    expect(growing.outsideBy(100, 30)).toBeCloseTo(37.5);
    expect(growing.has(100, 30)).toBe(true);
    expect(growing.outsideBy(100, 30)).toBe(0);
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
    const m = new RecoveryMonitor(area);
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

  test('a loaded game or a teleport restarts sampling at the new place and drops a pending lost state', () => {
    const m = new RecoveryMonitor(area);
    for (let t = 0; t < 5; t += 0.1) m.update(probe(t, 30), 2);
    for (let t = 5; !m.lost; t += 0.1) m.update(probe(t, 131), 2);
    m.restart(20);
    expect(m.lost).toBe(null);
    expect(m.armed).toBe(false);
    expect(m.samples).toEqual([]);
    expect(m.update(probe(20.1, 131), 2)).toBe(false); // the grace period after a placement
    // Placed in mid-air: the fall to the ground is not a loss, and the first safe frame there arms and samples.
    for (let t = 20.5; t < 25; t += 0.1) expect(m.update(probe(t, 100, { z: 40, grounded: false, vy: -5, y: 30 }), 2)).toBe(false);
    expect(m.lost).toBe(null);
    m.update(probe(25, 100, { z: 40, heading: 1 }), 2);
    expect(m.armed).toBe(true);
    for (let t = 25.1; !m.lost; t += 0.1) m.update(probe(t, 131), 2);
    expect(m.target()).toMatchObject({ x: 100, z: 40, heading: 1 });
  });

  test('a player who never stood on safe ground is not lost: a spawn on the river bed', () => {
    const m = new RecoveryMonitor(area);
    for (let t = 0; t < 30; t += 0.1) expect(m.update(probe(t, 30 + t, { y: -3, waterDepth: 3 }), 2)).toBe(false);
    expect(m.armed).toBe(false);
    expect(m.lost).toBe(null);
    expect(m.samples).toEqual([]);
  });

  test('collision still streaming under the player is no fall; sinking below it once loaded is', () => {
    const m = new RecoveryMonitor(area);
    m.update(probe(0, 30), 2);
    // Over c_1_0 before its collision arrived: falling through for 10 s raises nothing.
    for (let t = 0.1; t < 10; t += 0.1) m.update(probe(t, 100, { loaded: false, grounded: false, vy: -20, y: 2 - t * 2 }), 2);
    expect(m.lost).toBe(null);
    m.update(probe(10, 100, { grounded: false, vy: -20, y: -18 }), 2);
    expect(m.lost?.reason).toBe('below');
    expect(m.target()).toMatchObject({ x: 30, z: 30 });
    // Off the area altogether (no collision there to wait for) still counts as lost.
    const off = new RecoveryMonitor(area);
    off.update(probe(0, 120), 2);
    for (let t = 0.1; t < 4; t += 0.1) off.update(probe(t, 120 + t * 5, { loaded: false, grounded: false, vy: -2 }), 2);
    expect(off.lost?.reason).toMatch(/outside|airborne/);
  });

  test('walking into the river: lost after 2 s under the surface, reset before the bank', () => {
    const m = new RecoveryMonitor(area);
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
    const m = new RecoveryMonitor(area);
    for (let t = 0; t < 2; t += 0.1) m.update(probe(t, 30), 2);
    for (let t = 2; t < 5.5; t += 0.1) m.update(probe(t, 30, { grounded: false, y: 40 - t }), 2);
    expect(m.lost?.reason).toBe('airborne');
    m.update(probe(5.6, 30), 2);
    expect(m.lost).toBe(null);
  });

  test('unsafe frames record no samples and do not arm the lost rules', () => {
    const m = new RecoveryMonitor(area);
    m.update(probe(0, 30, { loaded: false }), 2);
    m.update(probe(1, 30, { vy: -3 }), 2);
    m.update(probe(2, 30, { waterDepth: 0.1 }), 2);
    m.update(probe(3, 30, { steady: false, inVehicle: true }), 2);
    m.update(probe(4, 100, { z: 100 }), 2); // the missing corner tile
    expect(m.samples).toEqual([]);
    expect(m.armed).toBe(false);
    m.update(probe(5, 40, { waterDepth: -3 }), 2); // on a bridge above the river
    expect(m.samples.map(s => s.x)).toEqual([40]);
    expect(m.armed).toBe(true);
  });
});

describe('developer fly mode', () => {
  test('hold: nobody is lost while flying; the rules re-arm at the first grounded contact with the old samples', () => {
    const m = new RecoveryMonitor(area);
    for (let t = 0; t <= 4; t++) m.update(probe(t, 30), 0);
    expect(m.armed).toBe(true);
    const samples = m.samples.length;
    // Flying 200 m up and outside the area for a minute: held every frame, nothing sampled, never lost.
    for (let t = 5; t < 65; t++) {
      m.hold();
      expect(m.update(probe(t, 300, { y: 200, grounded: false, vy: 0 }), 0)).toBe(false);
    }
    expect(m.lost).toBeNull();
    expect(m.armed).toBe(false);
    // Flight off: a fall without ground stays unarmed until the first grounded contact.
    for (let t = 65; t < 75; t++) expect(m.update(probe(t, 300, { y: 200 - (t - 65) * 10, grounded: false, vy: -10 }), 0)).toBe(false);
    expect(m.armed).toBe(false);
    expect(m.samples.length).toBe(samples);
    // Landing in the river: grounded, so the rules arm with the samples from before the flight and the river counts.
    m.update(probe(75, 30, { waterDepth: 1 }), 0);
    expect(m.armed).toBe(true);
    m.update(probe(78, 30, { waterDepth: 1 }), 0);
    expect(m.lost?.reason).toBe('water');
    expect(m.target().x).toBe(30);
  });
  test('a teleport (restart) ends the hold: the first safe frame arms as usual', () => {
    const m = new RecoveryMonitor(area);
    m.hold();
    m.restart(0);
    m.update(probe(1, 30), 0);
    expect(m.armed).toBe(true);
  });
});
