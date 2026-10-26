import { describe, expect, it } from 'vitest';
import { EMPTY_HUD_STATE, emptyHudSource, type MissionHudState, type Router, type WorldPoint } from '../../src/gameplay/contract';
import {
  BASE_PAY, LICENCE_PRICE, MAX_TIP, PAR_HANDLING_SECONDS, START_MONEY, VEHICLE_PRICE, parSeconds, tip,
} from '../../src/gameplay/economy';
import { DIALOGUE, dialogueLine, lineText } from '../../src/gameplay/content/dialogue';
import { DELIVERIES } from '../../src/gameplay/content/deliveries';
import { DEALER, MARKET, PIZZERIA } from '../../src/gameplay/content/locations';
import {
  INTERACT_RADIUS, MissionSystem, OWN_VEHICLE_ID, SAVE_KEY, lineSeconds, type KeyValueStore, type MissionFrame,
} from '../../src/gameplay/missions/missions';

/** Synthetic router: an L-shaped path (x first, then z), so route length differs from the straight line. */
const lRouter: Router = {
  route: (from, to) => [from, { x: to.x, z: from.z }, to],
  distance: path => path.slice(1).reduce((sum, p, i) => sum + Math.hypot(p.x - path[i].x, p.z - path[i].z), 0),
};
const lDistance = (a: WorldPoint, b: WorldPoint) => Math.abs(a.x - b.x) + Math.abs(a.z - b.z);

class MemoryStore implements KeyValueStore {
  data = new Map<string, string>();
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
}

function harness(store: KeyValueStore = new MemoryStore()) {
  const missions = new MissionSystem({ router: lRouter, storage: store });
  const states: MissionHudState[] = [];
  missions.subscribe(s => states.push(s));
  const frame = (position: WorldPoint, extra: Partial<MissionFrame> = {}): MissionFrame =>
    ({ position, interact: false, input: 'keyboard', vehicleId: null, ...extra });
  const at = (position: WorldPoint, dt = 0.1, extra: Partial<MissionFrame> = {}) => missions.update(dt, frame(position, extra));
  const press = (position: WorldPoint, extra: Partial<MissionFrame> = {}) => at(position, 0.1, { ...extra, interact: true });
  /** Lets the dialogue queue drain without moving. */
  const wait = (position: WorldPoint, seconds: number) => { for (let t = 0; t < seconds; t += 0.5) at(position, 0.5); };
  return { missions, states, at, press, wait, store, last: () => states[states.length - 1] };
}
const near = (p: WorldPoint, dx = 1): WorldPoint => ({ x: p.x + dx, z: p.z });
const far: WorldPoint = { x: -500, z: -500 };

describe('contract', () => {
  it('emptyHudSource reports the empty state once and unsubscribes', () => {
    const seen: MissionHudState[] = [];
    const off = emptyHudSource.subscribe(s => seen.push(s));
    off();
    expect(seen).toEqual([EMPTY_HUD_STATE]);
  });
});

describe('economy', () => {
  it('pays the full tip up to 70 % of par and nothing from 130 %, linearly between', () => {
    expect(tip(0, 100)).toBe(MAX_TIP);
    expect(tip(70, 100)).toBe(MAX_TIP);
    expect(tip(100, 100)).toBe(MAX_TIP / 2);
    expect(tip(115, 100)).toBe(2);
    expect(tip(130, 100)).toBe(0);
    expect(tip(500, 100)).toBe(0);
    for (let e = 0; e < 140; e += 1) expect(tip(e, 100)).toBeGreaterThanOrEqual(tip(e + 1, 100));
  });
  it('derives par time from route distance and the owned vehicle', () => {
    expect(parSeconds(140, 'none')).toBeCloseTo(100 + PAR_HANDLING_SECONDS);
    expect(parSeconds(500, 'bicycle')).toBeCloseTo(100 + PAR_HANDLING_SECONDS);
    expect(parSeconds(900, 'car')).toBeCloseTo(100 + PAR_HANDLING_SECONDS);
  });
});

describe('dialogue content', () => {
  it('has unique well-formed ids and known speakers', () => {
    const ids = DIALOGUE.lines.map(l => l.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const line of DIALOGUE.lines) {
      expect(line.id).toMatch(/^[a-z0-9_]+$/);
      expect(DIALOGUE.speakers[line.speaker], line.id).toBeDefined();
      expect(line.text.trim()).toBe(line.text);
    }
  });
  it('references only existing lines from content', () => {
    for (const d of DELIVERIES) for (const id of [d.hint, d.handover, d.customer.fast, d.customer.ok, d.customer.slow]) {
      expect(() => dialogueLine(id), id).not.toThrow();
    }
    for (let t = 0; t <= MAX_TIP; t++) expect(lineText(`paid_tip_${t}`)).toContain(`${BASE_PAY} €`);
    for (let t = 1; t <= MAX_TIP; t++) expect(lineText(`paid_tip_${t}`)).toContain(`tip ${t} €`);
  });
  it('quotes the economy prices in the prompts and intro', () => {
    for (const input of ['pad', 'key']) {
      expect(lineText(`prompt_buy_licence_${input}`)).toContain(`${LICENCE_PRICE} €`);
      expect(lineText(`prompt_buy_bicycle_${input}`)).toContain(`${VEHICLE_PRICE.bicycle} €`);
      expect(lineText(`prompt_buy_car_${input}`)).toContain(`${VEHICLE_PRICE.car} €`);
    }
    expect(lineText('intro_3')).toContain(`${START_MONEY} euros`);
    expect(dialogueLine('prompt_take_pizza_pad').voiced).toBe(false);
  });
  it('places every delivery at a distinct street distance', () => {
    const metres = DELIVERIES.map(d => lDistance(PIZZERIA.point, d.point));
    expect([...metres].sort((a, b) => a - b)).toEqual(metres);
  });
});

describe('mission state machine', () => {
  it('starts idle and asks for the licence after begin()', () => {
    const h = harness();
    expect(h.missions.isNewGame).toBe(true);
    expect(h.last()).toMatchObject({ activeTargetId: null, money: START_MONEY, timerSeconds: null, message: null });
    h.missions.begin();
    h.at(far);
    expect(h.missions.phase).toBe('licence_needed');
    expect(h.last()).toMatchObject({ activeTargetId: MARKET.id, message: lineText('hint_licence') });
    expect(h.last().targets.map(t => t.id)).toEqual([PIZZERIA.id, DEALER.id, MARKET.id]);
  });

  it('plays a licence, pickup and delivery round with pay, tip and prompts', () => {
    const h = harness();
    h.missions.begin();
    // Approach: the seller greets, then the prompt names the button for the active input device.
    h.at(near(MARKET.point));
    expect(h.last().message).toBe(lineText('market_offer'));
    h.wait(near(MARKET.point), 15);
    expect(h.last().message).toBe(lineText('prompt_buy_licence_key'));
    h.at(near(MARKET.point), 0.1, { input: 'gamepad' });
    expect(h.last().message).toBe(lineText('prompt_buy_licence_pad'));
    h.press(near(MARKET.point));
    expect(h.missions.phase).toBe('at_pizzeria');
    expect(h.last()).toMatchObject({ money: START_MONEY - LICENCE_PRICE, activeTargetId: PIZZERIA.id, message: lineText('market_sold') });
    expect(h.last().targets.map(t => t.id)).not.toContain(MARKET.id);
    h.wait(far, 15);
    expect(h.last().message).toBe(lineText('hint_pizzeria'));

    h.at(near(PIZZERIA.point));
    expect(h.last().message).toBe(lineText('pizzeria_first'));
    h.wait(near(PIZZERIA.point), 15);
    expect(h.last().message).toBe(lineText('prompt_take_pizza_key'));
    h.press(near(PIZZERIA.point));
    const d = DELIVERIES[0];
    const par = parSeconds(lDistance(PIZZERIA.point, d.point), 'none');
    expect(h.missions.phase).toBe('carrying');
    expect(h.last()).toMatchObject({ activeTargetId: d.id, message: lineText(d.handover), timerSeconds: Math.ceil(2 * par) });
    expect(h.last().targets.find(t => t.id === d.id)).toMatchObject({ kind: 'dropoff', point: d.point });
    h.wait(far, 15);
    expect(h.last().message).toBe(lineText(d.hint));
    expect(h.last().timerSeconds).toBeLessThan(Math.ceil(2 * par));

    h.at(near(d.point, INTERACT_RADIUS - 0.1));
    expect(h.last().message).toBe(lineText('prompt_hand_over_key'));
    const elapsed = h.missions.snapshot().elapsed;
    h.press(near(d.point));
    const expectedTip = tip(elapsed + 0.1, par);
    expect(expectedTip).toBe(MAX_TIP);
    expect(h.missions.phase).toBe('delivered');
    expect(h.last()).toMatchObject({ money: START_MONEY - LICENCE_PRICE + BASE_PAY + expectedTip, timerSeconds: null,
      message: lineText(d.customer.fast), activeTargetId: PIZZERIA.id });
    h.wait(near(d.point), lineSeconds(lineText(d.customer.fast)) + 0.1);
    expect(h.last().message).toBe(lineText(`paid_tip_${expectedTip}`));
    h.wait(near(d.point), 15);
    expect(h.missions.phase).toBe('at_pizzeria');
    expect(h.last().message).toBe(lineText('hint_pizzeria'));
    expect(h.missions.currentDelivery).toBe(DELIVERIES[1]);
  });

  it('does nothing outside the interaction radius', () => {
    const h = harness();
    h.missions.begin();
    h.press(near(MARKET.point, INTERACT_RADIUS + 0.2));
    expect(h.missions.licence).toBe(false);
    expect(h.last().money).toBe(START_MONEY);
  });

  it('fails a delivery after twice the par time and moves on to the next address', () => {
    const h = harness();
    h.missions.begin();
    h.press(near(MARKET.point));
    h.press(near(PIZZERIA.point));
    const par = parSeconds(lDistance(PIZZERIA.point, DELIVERIES[0].point), 'none');
    h.wait(far, 2 * par + 1);
    expect(h.missions.phase === 'failed' || h.missions.phase === 'at_pizzeria').toBe(true);
    expect(h.last().money).toBe(START_MONEY - LICENCE_PRICE);
    expect(h.missions.currentDelivery).toBe(DELIVERIES[1]);
    h.wait(far, 15);
    h.at(near(PIZZERIA.point));
    expect(h.last().message).toBe(lineText('pizzeria_after_fail'));
  });

  it('pays no tip for a slow but completed delivery', () => {
    const h = harness();
    h.missions.begin();
    h.press(near(MARKET.point));
    h.press(near(PIZZERIA.point));
    const d = DELIVERIES[0];
    const par = parSeconds(lDistance(PIZZERIA.point, d.point), 'none');
    h.wait(far, 1.5 * par);
    const before = h.last().money;
    h.press(near(d.point));
    expect(h.last()).toMatchObject({ money: before + BASE_PAY, message: lineText(d.customer.slow) });
  });

  it('repeats the deliveries in order after the last one', () => {
    const h = harness();
    h.missions.begin();
    h.press(near(MARKET.point));
    const order: string[] = [];
    for (let i = 0; i < DELIVERIES.length + 2; i++) {
      h.press(near(PIZZERIA.point));
      order.push(h.missions.currentDelivery.id);
      h.press(near(h.missions.currentDelivery.point));
    }
    expect(order).toEqual([...DELIVERIES, DELIVERIES[0], DELIVERIES[1]].map(d => d.id));
  });

  it('sells vehicles only after the licence, and only when affordable', () => {
    const h = harness();
    h.missions.begin();
    h.wait(near(DEALER.point), 15);
    expect(h.last().message).toBe(lineText('notice_licence_first'));
    expect(h.press(near(DEALER.point)).bought).toBeNull();
    expect(h.last().message).toBe(lineText('dealer_no_licence'));
    h.press(near(MARKET.point)); // 40 € left
    h.wait(near(DEALER.point), 15);
    expect(h.last().message).toBe(lineText('notice_bicycle_price'));
    h.press(near(DEALER.point));
    expect(h.missions.vehicle).toBe('none');
    while (h.missions.money < VEHICLE_PRICE.bicycle) {
      h.press(near(PIZZERIA.point));
      h.press(near(h.missions.currentDelivery.point));
    }
    h.wait(near(DEALER.point), 15);
    expect(h.last().message).toBe(lineText('prompt_buy_bicycle_key'));
    const money = h.missions.money;
    expect(h.press(near(DEALER.point)).bought).toBe('bicycle');
    expect(h.missions).toMatchObject({ vehicle: 'bicycle', money: money - VEHICLE_PRICE.bicycle });
    expect(h.last().message).toBe(lineText('dealer_sold_bicycle'));
    h.wait(near(DEALER.point), 15);
    expect(h.last().message).toBe(lineText('notice_car_price'));
  });

  it('uses the owned vehicle for par time and allows only that vehicle while carrying', () => {
    const h = harness();
    h.missions.begin();
    h.press(near(MARKET.point));
    while (h.missions.money < VEHICLE_PRICE.bicycle) {
      h.press(near(PIZZERIA.point));
      h.press(near(h.missions.currentDelivery.point));
    }
    h.press(near(DEALER.point));
    expect(h.missions.mayDrive('parked-1')).toBe(true); // free roam: any car
    // Picking up from a borrowed car is refused with a notice.
    h.wait(near(PIZZERIA.point), 15);
    h.at(near(PIZZERIA.point), 0.1, { vehicleId: 'parked-1' });
    expect(h.last().message).toBe(lineText('notice_own_vehicle_only'));
    h.press(near(PIZZERIA.point), { vehicleId: 'parked-1' });
    expect(h.missions.phase).not.toBe('carrying');
    h.press(near(PIZZERIA.point), { vehicleId: OWN_VEHICLE_ID });
    expect(h.missions.phase).toBe('carrying');
    const d = h.missions.currentDelivery;
    expect(h.missions.snapshot().par).toBeCloseTo(parSeconds(lDistance(PIZZERIA.point, d.point), 'bicycle'));
    expect(h.missions.mayDrive(OWN_VEHICLE_ID)).toBe(true);
    expect(h.missions.mayDrive('parked-1')).toBe(false);
    expect(h.last().message).toBe(lineText('notice_own_vehicle_only'));
  });

  it('continues a saved game after a reload, including the running timer', () => {
    const store = new MemoryStore();
    const a = harness(store);
    a.missions.begin();
    a.press(near(MARKET.point));
    a.press(near(PIZZERIA.point));
    a.wait(far, 10);
    const saved = JSON.parse(store.getItem(SAVE_KEY)!);
    expect(saved).toMatchObject({ phase: 'carrying', licence: true, money: START_MONEY - LICENCE_PRICE });
    const b = harness(store);
    expect(b.missions.isNewGame).toBe(false);
    b.missions.begin();
    b.at(far, 0);
    expect(b.missions.phase).toBe('carrying');
    expect(b.last().timerSeconds).toBe(a.last().timerSeconds);
    expect(b.last().activeTargetId).toBe(DELIVERIES[0].id);
  });

  it('falls back to a new game on a corrupt save', () => {
    const store = new MemoryStore();
    store.setItem(SAVE_KEY, '{"version":1,"phase":"carrying","money":-5}');
    expect(harness(store).missions).toMatchObject({ isNewGame: true, money: START_MONEY });
    store.setItem(SAVE_KEY, 'not json');
    expect(harness(store).missions.isNewGame).toBe(true);
  });

  it('notifies subscribers only on change and stops after unsubscribe', () => {
    const missions = new MissionSystem({ router: lRouter, storage: null });
    const seen: MissionHudState[] = [];
    const off = missions.subscribe(s => seen.push(s));
    expect(seen).toHaveLength(1);
    missions.begin();
    expect(seen).toHaveLength(2);
    missions.update(0.016, { position: far, interact: false, input: 'keyboard', vehicleId: null });
    missions.update(0.016, { position: far, interact: false, input: 'keyboard', vehicleId: null });
    expect(seen).toHaveLength(2);
    off();
    missions.update(0.1, { position: near(MARKET.point), interact: true, input: 'keyboard', vehicleId: null });
    expect(seen).toHaveLength(2);
    expect(missions.licence).toBe(true);
  });
});
