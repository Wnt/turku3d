import { describe, expect, it } from 'vitest';
import { MissionSystem, type MissionFrame, type PaidDropoff } from '../../src/gameplay/missions/missions';
import { freshSave, parseSave } from '../../src/gameplay/missions/save';
import { drawLongOrder } from '../../src/gameplay/missions/orders';
import { basePay, parSeconds, tip, deliveryDeadline } from '../../src/gameplay/economy';
import { PIZZERIA } from '../../src/gameplay/content/locations';
import type { PoolAddress } from '../../src/gameplay/content/addresses';
import { lRouter } from './missionHarness';

const pool: PoolAddress[] = [100, 3000, 5000].map((m, i) => ({ id: `district-${i}`, address: `District ${i}`,
  tile: `t0_${i}_0`, point: { x: m, z: 0 }, street: { x: m, z: 1 }, foot: m, car: m, landmark: i > 0 }));
const frame = (extra: Partial<MissionFrame> = {}): MissionFrame => ({ position: PIZZERIA.point,
  interact: false, vehicleId: 'player', input: 'keyboard', ...extra });
function game(shortDeliveries = 39) {
  const m = new MissionSystem({ router: lRouter, addresses: pool, seed: 1 });
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
      expect(m.completedRounds).toBe(3);
      pickup(m);
      expect(m.permit).toBe(19);
      expect(m.longUnlocked).toBe(false);
      finish(m, fail);
      expect(m.completedRounds).toBe(4);
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
    expect(m.shop!.options.find(o => o.id === 'take_long_order')!.reason).toContain('four rounds');
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
});
