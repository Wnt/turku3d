import { describe, expect, it } from 'vitest';
import { MissionSystem, type MissionFrame, type PaidDropoff } from '../../src/gameplay/missions/missions';
import { freshSave, parseSave } from '../../src/gameplay/missions/save';
import { drawLongOrder } from '../../src/gameplay/missions/orders';
import { basePay, parSeconds, tip, deliveryDeadline, LONG_MAX_METRES } from '../../src/gameplay/economy';
import { PIZZERIA } from '../../src/gameplay/content/locations';
import { addressSlug, type PoolAddress } from '../../src/gameplay/content/addresses';
import { LANDMARKS, withLandmarks } from '../../src/gameplay/content/landmarks';
import type { Router, WorldPoint } from '../../src/gameplay/contract';
import { lRouter } from './missionHarness';

const pool: PoolAddress[] = [100, 3000, 5000].map((m, i) => ({ id: `district-${i}`, address: `District ${i}`,
  tile: `t0_${i}_0`, point: { x: m, z: 0 }, street: { x: m, z: 1 }, foot: m, car: m, landmark: i > 0 }));
const frame = (extra: Partial<MissionFrame> = {}): MissionFrame => ({ position: PIZZERIA.point,
  interact: false, vehicleId: 'player', input: 'keyboard', ...extra });
function game(shortDeliveries = 3, addresses: readonly PoolAddress[] = pool, router: Router = lRouter) {
  const m = new MissionSystem({ router, addresses, seed: 1 });
  m.restore(JSON.stringify({ ...freshSave(1), phase: 'at_pizzeria', permit: 20, vehicle: 'car',
    delivery: shortDeliveries, shortDeliveries, money: 100 }));
  return m;
}
function pickup(m: MissionSystem, long = false) {
  m.update(0, frame({ interact: true }));
  m.update(0, frame({ choose: long ? 'take_long_order' : 'take_order' }));
}
function finish(m: MissionSystem, fail = false) {
  if (fail) m.update(deliveryDeadline(m.snapshot().par) + 1, frame({ position: { x: -500, z: -500 } }));
  else while (m.phase === 'carrying') m.update(0, frame({ position: m.currentStop.point, interact: true }));
}

describe('long-range deliveries', () => {
  it('counts finished short orders, including failed orders, rather than pickups or remaining permit stock', () => {
    for (const fail of [false, true]) {
      const m = game();
      pickup(m);
      expect(m.permit).toBe(19);
      expect(m.longUnlocked).toBe(false);
      finish(m, fail);
      expect(m.longUnlocked).toBe(true);
      // Queue drains; the saved once-only announcement is delivered after the result.
      m.update(100, frame({ position: { x: -500, z: -500 } }));
      expect(m.snapshot().hud.message).toContain('Long-range deliveries are now available');
      const saved = m.serialize();
      m.restore(saved);
      m.update(0, frame());
      expect(m.longUnlocked).toBe(true);
      expect(m.snapshot().dialogue).not.toContain('long_unlocked');
      m.restore(null);
      expect(m.longUnlocked).toBe(false);
    }
  });

  it('migrates old saves with completed counts and preserves a running long order and its timer', () => {
    const old = { ...freshSave(7), version: 3, delivery: 40, shortDeliveries: undefined };
    expect(parseSave(JSON.stringify(old), 0)).toMatchObject({ version: 4, shortDeliveries: 40, longAnnounced: false });
    const m = game(40); pickup(m, true);
    m.update(20, frame());
    const before = m.snapshot(); m.restore(m.serialize());
    expect(m.snapshot()).toMatchObject({ job: 'long', elapsed: 20, par: before.par, order: before.order });
  });

  it('offers both jobs after unlock, with long distance pay and a tighter par; pays zero on cancellation', () => {
    for (const fail of [false, true]) {
      const m = game(40);
      m.update(0, frame({ interact: true }));
      const long = m.shop!.options.find(o => o.id === 'take_long_order')!;
      expect(long.enabled).toBe(true);
      expect(m.shop!.options.find(o => o.id === 'take_order')!.enabled).toBe(true);
      expect(long.quote!.metres).toBeGreaterThanOrEqual(2500);
      expect(long.quote!.pay).toBe(basePay(long.quote!.metres, 'long'));
      expect(long.quote!.par).toBeLessThan(parSeconds(long.quote!.metres, 'car'));
      m.update(0, frame({ choose: 'take_long_order' }));
      const money = m.money, quote = long.quote!;
      finish(m, fail);
      expect(m.money - money).toBe(fail ? 0 : quote.pay! + 8);
      expect(m.snapshot()).toMatchObject({ shortDeliveries: 40, longDeliveries: 1 });
    }
  });

  it('never substitutes a short, disconnected or non-drivable destination and prevents locked pickup', () => {
    expect(drawLongOrder([pool[0], { ...pool[1], car: null }], 0, 1, new Set())).toBeNull();
    const a = drawLongOrder(pool, 0, 1, new Set())!;
    expect(drawLongOrder(pool, 1, 1, new Set([a.id]))!.id).not.toBe(a.id);
    const m = game(); pickup(m, true);
    expect(m.phase).toBe('at_pizzeria');
    expect(m.permit).toBe(20);
    expect(m.shop!.options.find(o => o.id === 'take_long_order')!.reason).toContain('four short deliveries');
  });

  it('emits the long job and exact road distance for multiplayer payout validation', () => {
    const paid: PaidDropoff[] = [];
    const m = new MissionSystem({ router: lRouter, addresses: pool, onPaid: drop => paid.push(drop) });
    m.restore(game(40).serialize()); pickup(m, true);
    const metres = m.currentStop.pool!.car!;
    finish(m);
    expect(paid).toHaveLength(1);
    expect(paid[0]).toMatchObject({ job: 'long', routeMeters: metres, payout: basePay(metres, 'long'), tip: 8 });
  });

  it('scales salary with kilometres and requires a faster drive for the full tip', () => {
    expect(basePay(3000, 'long')).toBe(60);
    expect(basePay(5000, 'long')).toBe(84);
    expect(basePay(500)).toBe(6);
    const short = parSeconds(4000, 'car'), long = parSeconds(4000, 'car', 'long');
    expect(tip(0.7 * long, long)).toBe(8);
    expect(tip(0.7 * short, long)).toBeLessThan(8);
    expect(tip(1.3 * long, long)).toBe(0);
    // A clean scooter run at its 45 km/h top speed can still earn a full tip.
    expect(tip(6000 / 12.5 + 10, parSeconds(6000, 'scooter', 'long'))).toBe(8);
  });

  // The shared local tile farm serves no /coarse/navigation.json: the pool is empty and the street graph is only the
  // resident tiles around the player.
  const LOCAL_M = 400;
  const local = (p: WorldPoint) => Math.hypot(p.x - PIZZERIA.point.x, p.z - PIZZERIA.point.z) <= LOCAL_M;
  const localRouter: Router = { route: (a, b) => local(a) && local(b) ? lRouter.route(a, b) : null, distance: lRouter.distance };
  const nearby: PoolAddress[] = [{ id: 'near-1', address: 'Near 1', tile: 't0_0_0', point: { x: 177, z: 187 },
    street: { x: 177, z: 190 }, foot: 120, car: 130 }];

  it('quotes a city-wide long job from the landmark fallbacks with only a small local street graph', () => {
    for (const served of [[], nearby]) {
      const paid: PaidDropoff[] = [];
      const m = new MissionSystem({ router: localRouter, addresses: withLandmarks(served), onPaid: drop => paid.push(drop) });
      m.restore(game(4).serialize());
      m.update(0, frame({ interact: true }));
      const long = m.shop!.options.find(o => o.id === 'take_long_order')!;
      expect(long.reason).toBeNull();
      const target = LANDMARKS.find(l => l.name === long.quote!.address)!;
      expect(long.quote).toMatchObject({ metres: target.fallback!.car, stops: 1, pay: basePay(target.fallback!.car, 'long'),
        par: parSeconds(target.fallback!.car, 'car', 'long') });
      m.update(0, frame({ choose: 'take_long_order' }));
      expect(m.phase).toBe('carrying');
      expect(localRouter.route(PIZZERIA.point, m.currentStop.point)).toBeNull();
      finish(m);
      // The multiplayer server accepts a long payout of 24 + ceil(12/km) for 60 < routeMeters <= 7000.
      expect(paid[0]).toMatchObject({ job: 'long', routeMeters: long.quote!.metres, payout: long.quote!.pay });
      expect(paid[0].routeMeters).toBeGreaterThan(60);
      expect(paid[0].routeMeters).toBeLessThanOrEqual(LONG_MAX_METRES);
    }
  });

  it('keeps a served pool with landmarks authoritative and every fallback drivable from the pizzeria', () => {
    expect(withLandmarks(pool)).toEqual(pool);
    const fallbacks = withLandmarks([]);
    expect(fallbacks.map(a => a.id)).toEqual(LANDMARKS.map(l => addressSlug(l.name)));
    for (const a of fallbacks) {
      // A route is never shorter than the straight line: a moved pizzeria would need refreshed fallbacks.
      expect(a.car!).toBeGreaterThanOrEqual(Math.hypot(a.point.x - PIZZERIA.point.x, a.point.z - PIZZERIA.point.z));
      expect(a.car!).toBeLessThanOrEqual(LONG_MAX_METRES);
    }
  });

  it('announces the unlock only once a long job can be offered', () => {
    const m = game(3, [], localRouter);
    pickup(m);
    finish(m);
    m.update(100, frame({ position: { x: -500, z: -500 } }));
    expect(m.longUnlocked).toBe(true);
    expect(m.snapshot()).toMatchObject({ longAnnounced: false });
    expect(m.snapshot().dialogue).not.toContain('long_unlocked');
    m.update(0, frame({ interact: true }));
    expect(m.shop!.options.find(o => o.id === 'take_long_order')!.reason).toBe('No drivable long-range destination is available.');
    const later = new MissionSystem({ router: localRouter, addresses: withLandmarks([]) });
    later.restore(m.serialize());
    later.update(0, frame({ position: { x: -500, z: -500 } }));
    expect(later.snapshot().hud.message).toContain('Long-range deliveries are now available');
  });
});
