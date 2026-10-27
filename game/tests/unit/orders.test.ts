import { describe, expect, it } from 'vitest';
import type { Router, WorldPoint } from '../../src/gameplay/contract';
import { addressSlug, parseAddressPool, type PoolAddress } from '../../src/gameplay/content/addresses';
import { DIALOGUE, dialogueLine, lineText } from '../../src/gameplay/content/dialogue';
import { DELIVERIES } from '../../src/gameplay/content/deliveries';
import { MARKET, PIZZERIA } from '../../src/gameplay/content/locations';
import { BASE_PAY, DOUBLE_BONUS, MAX_TIP, parSeconds } from '../../src/gameplay/economy';
import { houseNumber } from '../../src/gameplay/minimap';
import {
  DOUBLE_EVERY, LADDER_MAX_M, LADDER_START_M, LADDER_STEP_M, drawOrder, isDouble, ladderMetres,
} from '../../src/gameplay/missions/orders';
import { MissionSystem, missionSummary, type MissionFrame } from '../../src/gameplay/missions/missions';

/** 40 addresses east of the pizzeria, 20 m apart along one street: `foot` = x offset, cars drive 10 % farther. */
const POOL: PoolAddress[] = Array.from({ length: 40 }, (_, i) => {
  const foot = 80 + 20 * i;
  return { id: `testikatu-${i + 1}`, address: `Testikatu ${i + 1}`, tile: 't0_0_0',
    point: { x: PIZZERIA.point.x + foot, z: PIZZERIA.point.z }, street: { x: PIZZERIA.point.x + foot, z: PIZZERIA.point.z - 2 },
    foot, car: Math.round(foot * 1.1) };
});
const straight: Router = {
  route: (from, to) => [from, to],
  distance: path => Math.hypot(path[1].x - path[0].x, path[1].z - path[0].z),
};

const near = (p: WorldPoint): WorldPoint => ({ x: p.x + 1, z: p.z });

/** A mission system on the synthetic pool, continued from a `serialize()` text when `saved` is given. */
function game(saved: string | null = null, pool: readonly PoolAddress[] = POOL, seed = 7) {
  const missions = new MissionSystem({ router: straight, addresses: pool, seed });
  if (saved !== null) missions.restore(saved);
  const frame = (position: WorldPoint, extra: Partial<MissionFrame> = {}): MissionFrame =>
    ({ position, interact: false, input: 'keyboard', vehicleId: null, ...extra });
  const at = (p: WorldPoint, extra: Partial<MissionFrame> = {}, dt = 0.1) => missions.update(dt, frame(p, extra));
  const choose = (point: WorldPoint, id: 'rent_permit' | 'take_order') => {
    at(near(point), { interact: true });
    at(near(point), { choose: id });
    at(near(point), { leave: true });
  };
  /** Takes the next order and hands over every pizza at once; returns the stop ids. */
  const deliver = () => {
    if (missions.permit === 0) choose(MARKET.point, 'rent_permit');
    choose(PIZZERIA.point, 'take_order');
    const stops = missions.order.map(o => o.id);
    for (let i = 0; i < stops.length; i++) at(near(missions.currentStop.point), { interact: true });
    for (let t = 0; t < 30; t += 0.5) at(near(PIZZERIA.point), {}, 0.5); // the result lines play out
    return stops;
  };
  missions.begin();
  return { missions, at, choose, deliver, saved: () => JSON.parse(missions.serialize()) };
}

describe('order generator', () => {
  it('climbs a capped distance ladder and makes every fifth order a double', () => {
    expect(ladderMetres(0)).toBe(LADDER_START_M);
    expect(ladderMetres(3)).toBe(LADDER_START_M + 3 * LADDER_STEP_M);
    expect(ladderMetres(1000)).toBe(LADDER_MAX_M);
    expect([0, 1, 2, 3, 4, 5, 9].map(isDouble)).toEqual([false, false, false, false, true, false, true]);
  });

  it('never repeats an address until the pool is exhausted, then starts over without the last order', () => {
    const delivered = new Set<string>();
    let n = 0;
    while (delivered.size < POOL.length) {
      const draw = drawOrder(POOL, n++, 3, delivered)!;
      expect(draw.exhausted).toBe(false);
      for (const id of draw.stops) { expect(delivered.has(id)).toBe(false); delivered.add(id); }
    }
    const last = [...delivered].slice(-2);
    const after = drawOrder(POOL, n, 3, delivered, new Set(), last)!;
    expect(after.exhausted).toBe(true);
    expect(after.stops.some(id => last.includes(id))).toBe(false);
  });

  it('keeps the scripted addresses for last and is deterministic per seed and order number', () => {
    const avoid = new Set(POOL.slice(0, 39).map(a => a.id));
    expect(drawOrder(POOL, 0, 1, new Set(), avoid)!.stops).toEqual(['testikatu-40']);
    expect(drawOrder(POOL, 6, 11, new Set())).toEqual(drawOrder(POOL, 6, 11, new Set()));
    expect(drawOrder([], 0, 1, new Set())).toBeNull();
  });

  it('draws near orders first and farther ones later, with occasional short hops', () => {
    const early: number[] = [], late: number[] = [];
    for (let seed = 0; seed < 200; seed++) {
      const foot = (n: number) => POOL.find(a => a.id === drawOrder(POOL, n, seed, new Set())!.stops[0])!.foot;
      early.push(foot(0));
      late.push(foot(12));
    }
    const mean = (v: number[]) => v.reduce((a, b) => a + b, 0) / v.length;
    const shortest = POOL[Math.ceil(POOL.length / 4) - 1].foot; // a short hop takes the nearest quarter
    expect(Math.max(...early)).toBeLessThanOrEqual(Math.max(LADDER_START_M, shortest));
    expect(mean(late)).toBeGreaterThan(mean(early) + 200);
    const short = late.filter(m => m <= shortest).length / late.length;
    expect(short).toBeGreaterThan(0.1);
    expect(short).toBeLessThan(0.35);
  });

  it('pairs a double with the nearest other address within reach', () => {
    const draw = drawOrder(POOL, DOUBLE_EVERY - 1, 5, new Set())!;
    expect(draw.stops).toHaveLength(2);
    const [a, b] = draw.stops.map(id => POOL.find(p => p.id === id)!);
    expect(Math.abs(a.foot - b.foot)).toBe(20);
    const lonely = [POOL[0], { ...POOL[1], point: { x: 5000, z: 5000 } }];
    expect(drawOrder(lonely, DOUBLE_EVERY - 1, 5, new Set())!.stops).toHaveLength(1);
  });
});

describe('address pool asset', () => {
  const json = (origin: [number, number]) => ({ format: 'turku3d-addresses', version: 1, frame: 'engine-xz', origin,
    addresses: [
      { id: 'linnankatu-9b', address: 'Linnankatu 9b', tile: 't0_0_0', point: [274.8, 54.3] as [number, number],
        street: [274.1, 52.0] as [number, number], foot: 244, car: 239 },
      { id: 'kaukana-1', address: 'Kaukana 1', tile: 't0_5_5', point: [2700, 2700] as [number, number],
        street: [2700, 2702] as [number, number], foot: 4000, car: null },
    ] });
  it('keeps addresses in published tiles and rejects another origin or format', () => {
    const pool = parseAddressPool(json([PIZZERIA.point.x, PIZZERIA.point.z]), tile => tile === 't0_0_0');
    expect(pool).toEqual([{ id: 'linnankatu-9b', address: 'Linnankatu 9b', tile: 't0_0_0', point: { x: 274.8, z: 54.3 },
      street: { x: 274.1, z: 52.0 }, foot: 244, car: 239 }]);
    expect(() => parseAddressPool(json([0, 0]), () => true)).toThrow(/not the pizzeria/);
    expect(() => parseAddressPool({ ...json([77.1, 187.6]), version: 2 }, () => true)).toThrow(/unsupported/);
  });
  it('slugs like the pipeline and labels the big map with the house number', () => {
    expect(addressSlug('Läntinen Rantakatu 13a')).toBe('lantinen-rantakatu-13a');
    expect(addressSlug('Itäinen  Rantakatu 15')).toBe('itainen-rantakatu-15');
    expect(houseNumber('Linnankatu 13a')).toBe('13a');
    expect(houseNumber('Black market under Auransilta')).toBeNull();
  });
});

describe('pool dialogue', () => {
  const ids = (prefix: string) => DIALOGUE.lines.filter(l => l.id.startsWith(prefix));
  it('has 12 voiced customer lines per tip band and 6 address-free pizzeria order lines', () => {
    for (const band of ['fast', 'ok', 'slow']) {
      const lines = ids(`customer_${band}_`);
      expect(lines).toHaveLength(12);
      expect(lines.every(l => l.voiced !== false && l.speaker === 'customer')).toBe(true);
    }
    expect(ids('pizzeria_order_')).toHaveLength(6);
  });
  it('fills placeholders only in unvoiced lines, so every voice clip stays address-free', () => {
    for (const line of DIALOGUE.lines) if (line.voiced !== false) expect(line.text).not.toMatch(/[{}]/);
    expect(dialogueLine('order_next').voiced).toBe(false);
    expect(lineText('hint_deliver_first', { address: 'Aurakatu 3', next: 'Linnankatu 9b' }))
      .toBe('Deliver the first pizza to Aurakatu 3, then Linnankatu 9b.');
    expect(lineText('paid_double_bonus')).toContain(`${DOUBLE_BONUS} €`);
  });
});

describe('pool orders in the mission system', () => {
  it('plays the scripted four, then pool addresses without repeats; a double pays two pizzas and the bonus', () => {
    const g = game();
    const served: string[][] = [];
    for (let i = 0; i < DELIVERIES.length; i++) served.push(g.deliver());
    expect(served.flat()).toEqual(DELIVERIES.map(d => d.id));
    for (let i = 0; i < 10; i++) {
      if (g.missions.permit === 0) g.choose(MARKET.point, 'rent_permit');
      const before = g.missions.money;
      const stops = g.deliver();
      served.push(stops);
      // Delivered instantly: full tips, and the second pizza of a double adds the bonus.
      expect(g.missions.money - before).toBe(stops.length * (BASE_PAY + MAX_TIP) + (stops.length > 1 ? DOUBLE_BONUS : 0));
    }
    const pool = served.slice(DELIVERIES.length).flat();
    expect(pool.every(id => id.startsWith('testikatu-'))).toBe(true);
    expect(new Set(pool).size).toBe(pool.length);
    expect(served[DELIVERIES.length + DOUBLE_EVERY - 1]).toHaveLength(2);
    expect(g.missions.pizzas).toBe(pool.length + DELIVERIES.length);
    expect(g.missions.shop).toBeNull();
    expect(g.saved()).toMatchObject({ version: 3, delivered: pool, order: [] });
    // The slot label names the last drop-off of the last order, a pool address too.
    const lastStops = served[served.length - 1];
    expect(g.missions.lastAddress).toBe(POOL.find(a => a.id === lastStops[lastStops.length - 1])!.address);
    expect(missionSummary(g.missions.serialize())).toMatchObject({ deliveries: served.length, lastAddress: g.missions.lastAddress });
  });

  it('quotes par from the published distance for the vehicle class and names the address in the hints', () => {
    const g = game(null, POOL.slice(10, 11)); // one address 280 m on foot, 308 m by car
    for (let i = 0; i < DELIVERIES.length; i++) g.deliver();
    g.choose(MARKET.point, 'rent_permit');
    g.at(near(PIZZERIA.point), { interact: true });
    const quote = g.missions.shop!.options.find(o => o.id === 'take_order')!.quote!;
    expect(quote).toEqual({ address: 'Testikatu 11', metres: 280, par: parSeconds(280, 'none'), stops: 1 });
    expect(g.missions.shop!.delivered).toBe(DELIVERIES.length);
    g.at(near(PIZZERIA.point), { choose: 'take_order' });
    expect(g.missions.snapshot().dialogue).toEqual([expect.stringMatching(/^pizzeria_order_[1-6]$/), 'order_next']);
    for (let t = 0; t < 30; t += 0.5) g.at({ x: 0, z: 0 }, {}, 0.5);
    expect(g.missions.snapshot().hud.message).toBe('Deliver the pizza to Testikatu 11.');
  });

  it('resumes a saved game with the delivered set and the carried pool order intact', () => {
    const a = game();
    for (let i = 0; i < DELIVERIES.length + 2; i++) a.deliver();
    a.choose(PIZZERIA.point, 'take_order');
    const carried = a.missions.currentStop.id;
    const text = a.missions.serialize();
    const saved = JSON.parse(text);
    expect(saved.delivered).toHaveLength(2);
    const b = game(text, POOL, 99);
    expect(b.missions).toMatchObject({ phase: 'carrying', currentStop: { id: carried } });
    expect(b.missions.snapshot()).toMatchObject({ delivered: saved.delivered, seed: saved.seed });
    // An address that left the published area returns the pizza and its permit delivery.
    const c = game(text, POOL.filter(p => p.id !== carried));
    expect(c.missions).toMatchObject({ phase: 'at_pizzeria', permit: saved.permit + 1 });
  });

  it('migrates a v2 save text: the count carries over, a carried scripted order stays and the pool starts unused', () => {
    // Byte-for-byte what MissionSystem.serialize() of main 12c4c1a writes into a save slot.
    const g = game('{"version":2,"phase":"carrying","money":64,"permit":7,"plates":false,"kidney":true,'
      + '"warning":false,"vehicle":"none","delivery":5,"elapsed":30.5,"par":240}');
    expect(g.missions).toMatchObject({ phase: 'carrying', money: 64, permit: 7, kidney: true,
      currentStop: { scripted: DELIVERIES[1] } });
    expect(g.missions.snapshot()).toMatchObject({ version: 3, delivery: 5, pizzas: 5, delivered: [], elapsed: 30.5,
      lastAddress: DELIVERIES[0].address });
    g.at(near(DELIVERIES[1].point), { interact: true });
    expect(g.saved()).toMatchObject({ version: 3, delivery: 6, order: [], lastAddress: DELIVERIES[1].address });
    const next = g.deliver();
    expect(next[0]).toMatch(/^testikatu-/);
  });
});
