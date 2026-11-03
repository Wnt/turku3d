import { describe, expect, it } from 'vitest';
import { MissionSystem, type MissionFrame, type PaidDropoff } from '../../src/gameplay/missions/missions';
import { freshSave, parseSave } from '../../src/gameplay/missions/save';
import { DOUBLE_EVERY, LONG_LEG_MAX_M, LONG_LEG_MIN_M, drawLimitedOrder, drawLongOrder } from '../../src/gameplay/missions/orders';
import {
  basePay, parSeconds, tip, deliveryDeadline, DOUBLE_BONUS, LIMITED_COOLDOWN_SECONDS, LIMITED_MIN_METRES,
  LIMITED_OFFER_SECONDS, LIMITED_PAY_FACTOR, LONG_MAX_METRES, MAX_TIP,
} from '../../src/gameplay/economy';
import { PIZZERIA } from '../../src/gameplay/content/locations';
import { addressSlug, type PoolAddress } from '../../src/gameplay/content/addresses';
import { LANDMARKS, withLandmarks } from '../../src/gameplay/content/landmarks';
import type { Router, WorldPoint } from '../../src/gameplay/contract';
import { lRouter } from './missionHarness';
import { PHRASES, addressSentence } from '../../src/gameplay/content/announcements';
import { lineText } from '../../src/gameplay/content/dialogue';

const pool: PoolAddress[] = [100, 2600, 2900].map((m, i) => ({ id: `district-${i}`, address: `District ${i}`,
  tile: `t0_${i}_0`, point: { x: m, z: 0 }, street: { x: m, z: 1 }, foot: m, car: m, landmark: i > 0 }));
const frame = (extra: Partial<MissionFrame> = {}): MissionFrame => ({ position: PIZZERIA.point,
  interact: false, vehicleId: 'player', input: 'keyboard', ...extra });
function game(shortDeliveries = 3, addresses: readonly PoolAddress[] = pool, router: Router = lRouter,
  extra: Record<string, unknown> = {}) {
  const m = new MissionSystem({ router, addresses, seed: 1 });
  m.restore(JSON.stringify({ ...freshSave(1), phase: 'at_pizzeria', permit: 20, vehicle: 'car',
    delivery: shortDeliveries, shortDeliveries, money: 100, ...extra }));
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
    const [a] = drawLongOrder(pool, 0, 1, new Set())!;
    expect(drawLongOrder(pool, 1, 1, new Set([a.id]))![0].id).not.toBe(a.id);
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

  it('offers exactly the curated landmarks, a served one with its fresh metres, and every fallback drivable', () => {
    // The served pool's own landmarks (district-1, district-2) are not curated; a curated one it carries keeps its entry.
    const curated: PoolAddress = { id: addressSlug(LANDMARKS[0].name), address: LANDMARKS[0].name, tile: 't0_9_9',
      point: { x: 2222, z: 0 }, street: { x: 2222, z: 2 }, foot: 3000, car: 3333, landmark: true };
    const offered = withLandmarks([...pool, curated]);
    expect(offered.map(a => a.id)).toEqual(['district-0', ...LANDMARKS.map(l => addressSlug(l.name))]);
    expect(offered[1]).toBe(curated);
    expect(offered.filter(a => a.landmark)).toHaveLength(LANDMARKS.length);
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

  // Landmarks on one street at x 2000 (L-shaped routes): a–b 1000 m and b–c 800 m are real drives, a–c (200 m) is next
  // door, d is across town from all of them. f (2600 m out) reaches 3 km with any partner but via b; e is 3.5 km out:
  // a LIMITED TIME destination only.
  const lm = (id: string, x: number, z: number, car: number): PoolAddress => ({ id, address: `Landmark ${id}`,
    tile: 't0_5_0', point: { x, z }, street: { x, z: z + 2 }, foot: car, car, landmark: true });
  const ring = [lm('a', 2000, 0, 1000), lm('b', 2000, 1000, 1200), lm('c', 2000, 200, 1100), lm('d', -4000, 0, 2500),
    lm('f', 2000, 2600, 2600), lm('e', 2000, 1800, 3500)];
  const lLeg = (a: PoolAddress, b: PoolAddress) => lRouter.distance(lRouter.route(a.point, b.point)!);

  it('makes every third long job a double whose second landmark is a real drive away, all below 3 km', () => {
    const seconds = new Set<string>();
    for (let seed = 0; seed < 60; seed++) {
      for (let n = 0; n < 9; n++) {
        const stops = drawLongOrder(ring, n, seed, new Set(), lLeg)!;
        if (n % DOUBLE_EVERY !== DOUBLE_EVERY - 1) { expect(stops).toHaveLength(1); continue; }
        // d has no landmark within reach and f none within 3 km in all: they stay single.
        expect(stops[0].id).not.toBe('e');
        expect(stops).toHaveLength(['d', 'f'].includes(stops[0].id) ? 1 : 2);
        if (stops.length < 2) continue;
        const leg = lLeg(stops[0], stops[1]);
        expect(leg).toBeGreaterThanOrEqual(LONG_LEG_MIN_M);
        expect(leg).toBeLessThanOrEqual(LONG_LEG_MAX_M);
        expect(stops[0].car! + leg).toBeLessThan(LIMITED_MIN_METRES);
        seconds.add(`${stops[0].id}>${stops[1].id}`);
      }
    }
    expect([...seconds].sort()).toEqual(['a>b', 'b>a', 'b>c', 'b>f', 'c>b']);
    // The second is an unused landmark when one is in reach.
    for (let seed = 0; seed < 20; seed++) {
      const stops = drawLongOrder(ring, 2, seed, new Set(['a']), lLeg)!;
      if (stops[0].id === 'c') expect(stops[1].id).toBe('b');
      if (stops[0].id === 'b') expect(['c', 'f']).toContain(stops[1].id);
    }
    expect(drawLongOrder(ring.slice(0, 1), 2, 1, new Set(), lLeg)).toHaveLength(1);
  });

  it('quotes, times and pays a long double like a short one: both legs, both pizzas, the double bonus', () => {
    const paid: PaidDropoff[] = [];
    const m = new MissionSystem({ router: lRouter, addresses: ring.slice(0, 3), onPaid: drop => paid.push(drop) });
    m.restore(game(40, ring.slice(0, 3), lRouter, { longDeliveries: 2, longAnnounced: true }).serialize());
    m.update(0, frame({ interact: true }));
    const quote = m.shop!.options.find(o => o.id === 'take_long_order')!.quote!;
    m.update(0, frame({ choose: 'take_long_order' }));
    const [first, second] = m.order;
    expect(m.order).toHaveLength(2);
    const leg = lLeg(first.pool!, second.pool!);
    expect(quote).toEqual({ address: `${first.address} + ${second.address}`, metres: first.pool!.car! + leg, stops: 2,
      par: parSeconds(first.pool!.car!, 'car', 'long') + parSeconds(leg, 'car', 'long'),
      pay: basePay(first.pool!.car!, 'long') + basePay(leg, 'long') });
    expect(m.snapshot()).toMatchObject({ job: 'long', par: quote.par, firstPar: parSeconds(first.pool!.car!, 'car', 'long') });
    const money = m.money;
    m.update(0, frame({ position: first.point, interact: true }));
    expect(m.snapshot()).toMatchObject({ phase: 'carrying', stop: 1, longDeliveries: 2 });
    expect(m.snapshot().dialogue).toEqual(['customer_fast', 'paid_long', 'second_address']);
    expect(m.snapshot().hud.message).not.toBeNull();
    // A reload between the two drop-offs keeps the double, its timer and the second stop.
    m.restore(m.serialize());
    expect(m.currentStop.id).toBe(second.id);
    m.update(0, frame({ position: second.point, interact: true }));
    expect(m.money - money).toBe(quote.pay! + 2 * MAX_TIP + DOUBLE_BONUS);
    expect(m.snapshot()).toMatchObject({ phase: 'delivered', longDeliveries: 3, delivered: [first.id, second.id] });
    expect(m.snapshot().dialogue).toEqual(['customer_fast', 'paid_long', 'paid_double_bonus']);
    // Each drop-off reports its own leg; the multiplayer server checks 24 + ceil(12/km) of it (+ the bonus).
    expect(paid).toMatchObject([
      { job: 'long', stopIndex: 0, routeMeters: first.pool!.car, payout: basePay(first.pool!.car!, 'long'), tip: MAX_TIP },
      { job: 'long', stopIndex: 1, routeMeters: leg, payout: basePay(leg, 'long') + DOUBLE_BONUS, tip: MAX_TIP },
    ]);
  });

  it('keeps a single long order carried in an older save single when the next draw would be a double', () => {
    const m = game(40, ring.slice(0, 3), lRouter, { longDeliveries: 2, longAnnounced: true, phase: 'carrying',
      job: 'long', order: ['b'], par: 300 });
    expect(m.order.map(o => o.id)).toEqual(['b']);
    const money = m.money;
    finish(m);
    expect(m.money - money).toBe(basePay(1200, 'long') + MAX_TIP);
    expect(m.snapshot()).toMatchObject({ longDeliveries: 3, job: 'short', order: [] });
  });

  it('draws LIMITED TIME runs of 3 km or more: far singles, and every third offer a double that reaches it', () => {
    const doubles = new Set<string>();
    for (let seed = 0; seed < 60; seed++) {
      for (let n = 0; n < 9; n++) {
        const stops = drawLimitedOrder(ring, n, seed, new Set(), lLeg)!;
        const total = stops[0].car! + (stops[1] ? lLeg(stops[0], stops[1]) : 0);
        expect(total).toBeGreaterThanOrEqual(LIMITED_MIN_METRES);
        if (n % DOUBLE_EVERY !== DOUBLE_EVERY - 1) { expect(stops.map(a => a.id)).toEqual(['e']); continue; }
        expect(stops).toHaveLength(2);
        expect(lLeg(stops[0], stops[1])).toBeGreaterThanOrEqual(LONG_LEG_MIN_M);
        expect(lLeg(stops[0], stops[1])).toBeLessThanOrEqual(LONG_LEG_MAX_M);
        doubles.add(`${stops[0].id}>${stops[1].id}`);
      }
    }
    // Every pair within reach whose whole route reaches 3 km, and no other.
    expect([...doubles].sort()).toEqual(['c>f', 'e>a', 'e>b', 'e>c', 'e>f', 'f>b', 'f>c', 'f>e']);
    expect(drawLimitedOrder(ring.slice(0, 3), 0, 1, new Set(), lLeg)).toBeNull();
    expect(drawLimitedOrder(ring.slice(0, 3), 2, 1, new Set(), lLeg)).toBeNull();
  });

  it('offers a LIMITED TIME job on a game clock: announced, declined by letting it run out, then a new one', () => {
    const m = game(40, ring, lRouter, { longAnnounced: true });
    m.update(0.1, frame({ position: { x: -500, z: -500 } }));
    const first = m.snapshot().limited;
    expect(first).toMatchObject({ n: 1, left: LIMITED_OFFER_SECONDS });
    expect(m.snapshot().dialogue).toEqual(['limited_offer']);
    const named = first.stops.map(id => ring.find(a => a.id === id)!.address);
    expect(PHRASES.limited.some(p => m.snapshot().hud.message === addressSentence(p, named[0], named[1]))).toBe(true);
    m.update(100, frame());
    m.update(0, frame({ interact: true }));
    const option = () => m.shop!.options.find(o => o.id === 'take_limited_order')!;
    expect(option()).toMatchObject({ enabled: true, reason: null });
    expect(option().quote).toMatchObject({ stops: 1, metres: 3500, pay: LIMITED_PAY_FACTOR * basePay(3500, 'long'),
      par: parSeconds(3500, 'car', 'long'), expires: LIMITED_OFFER_SECONDS - 100 });
    expect(option().detail).toContain('three kilometres');
    // A reload keeps the offer and its clock.
    m.restore(m.serialize());
    expect(m.snapshot().limited).toEqual({ ...first, left: LIMITED_OFFER_SECONDS - 100 });
    m.update(LIMITED_OFFER_SECONDS - 100, frame());
    m.update(0, frame({ interact: true }));
    expect(option()).toMatchObject({ enabled: false, reason: lineText('reason_no_limited'), quote: null });
    expect(m.snapshot().limited).toMatchObject({ stops: [], left: LIMITED_COOLDOWN_SECONDS });
    m.update(0, frame({ leave: true }));
    m.update(LIMITED_COOLDOWN_SECONDS - 1, frame({ position: { x: -500, z: -500 } }));
    expect(m.snapshot().limited).toMatchObject({ n: 1, stops: [], left: 1 });
    m.update(1, frame({ position: { x: -500, z: -500 } }));
    expect(m.snapshot().limited).toMatchObject({ n: 2, left: LIMITED_OFFER_SECONDS });
    expect(m.snapshot().dialogue).toEqual(['limited_offer']);
  });

  it('pays a taken LIMITED TIME double twice the long pay per leg, and starts the cooldown', () => {
    const paid: PaidDropoff[] = [];
    const m = new MissionSystem({ router: lRouter, addresses: ring, onPaid: drop => paid.push(drop) });
    m.restore(game(40, ring, lRouter, { longAnnounced: true, limited: { n: 2, stops: [], left: 0 } }).serialize());
    m.update(0.1, frame({ position: { x: -500, z: -500 } }));
    m.update(30, frame());
    m.update(0, frame({ interact: true }));
    const quote = m.shop!.options.find(o => o.id === 'take_limited_order')!.quote!;
    m.update(0, frame({ choose: 'take_limited_order' }));
    const [first, second] = m.order;
    const leg = lLeg(first.pool!, second.pool!);
    expect(m.snapshot()).toMatchObject({ phase: 'carrying', job: 'limited', limited: { n: 3, stops: [], left: LIMITED_COOLDOWN_SECONDS } });
    expect(quote).toMatchObject({ address: `${first.address} + ${second.address}`, metres: first.pool!.car! + leg, stops: 2,
      pay: basePay(first.pool!.car!, 'limited') + basePay(leg, 'limited') });
    expect(quote.metres).toBeGreaterThanOrEqual(LIMITED_MIN_METRES);
    const money = m.money;
    finish(m);
    expect(m.money - money).toBe(quote.pay! + 2 * MAX_TIP + DOUBLE_BONUS);
    expect(m.snapshot()).toMatchObject({ phase: 'delivered', job: 'short', longDeliveries: 0 });
    expect(m.snapshot().dialogue).toEqual(['customer_fast', 'paid_limited', 'paid_double_bonus']);
    expect(paid).toMatchObject([
      { job: 'limited', routeMeters: first.pool!.car, payout: 2 * basePay(first.pool!.car!, 'long') },
      { job: 'limited', routeMeters: leg, payout: 2 * basePay(leg, 'long') + DOUBLE_BONUS },
    ]);
  });

  it('keeps LIMITED TIME runs out of the normal long job and needs a vehicle like it', () => {
    const m = game(40, ring, lRouter, { longAnnounced: true, vehicle: 'none' });
    m.update(0.1, frame({ position: { x: -500, z: -500 }, vehicleId: null }));
    m.update(0, frame({ interact: true, vehicleId: null }));
    const limited = m.shop!.options.find(o => o.id === 'take_limited_order')!;
    expect(limited).toMatchObject({ enabled: false, reason: lineText('reason_long_vehicle') });
    for (let n = 0; n < 12; n++) {
      const g = game(40, ring, lRouter, { longDeliveries: n });
      g.update(0, frame({ interact: true }));
      expect(g.shop!.options.find(o => o.id === 'take_long_order')!.quote!.metres).toBeLessThan(LIMITED_MIN_METRES);
    }
    const locked = game(3, ring);
    locked.update(500, frame({ position: { x: -500, z: -500 } }));
    expect(locked.snapshot().limited).toMatchObject({ n: 0, stops: [] });
  });
});
