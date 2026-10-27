import { describe, expect, it } from 'vitest';
import { EMPTY_HUD_STATE, emptyHudSource, type MissionHudState, type Router, type WorldPoint } from '../../src/gameplay/contract';
import {
  BASE_PAY, KIDNEY_PAYOUT, KIDNEY_RUN_FACTOR, MAX_TIP, PAR_HANDLING_SECONDS, PERMIT_DELIVERIES, PERMIT_MAX,
  PERMIT_PRICE, PERMIT_WARN_AT, PLATES_PRICE, SLICE_PRICE, SLICE_SECONDS, START_MONEY, VEHICLE_PRICE, WARNING_PRICE,
  parSeconds, sellBackPrice, tip,
} from '../../src/gameplay/economy';
import { DIALOGUE, dialogueLine, lineText } from '../../src/gameplay/content/dialogue';
import { DELIVERIES } from '../../src/gameplay/content/deliveries';
import { DEALER, MARKET, PIZZERIA } from '../../src/gameplay/content/locations';
import { SHOPS, ZONE_LABELS, optionDetail, optionLabel, type OptionId } from '../../src/gameplay/content/shops';
import {
  INTERACT_RADIUS, MissionSystem, OWN_VEHICLE_ID, SCOOTER_ID, ownedVehicleId, lineSeconds, missionSummary, type MissionFrame,
} from '../../src/gameplay/missions/missions';

/** Synthetic router: an L-shaped path (x first, then z), so route length differs from the straight line. */
const lRouter: Router = {
  route: (from, to) => [from, { x: to.x, z: from.z }, to],
  distance: path => path.slice(1).reduce((sum, p, i) => sum + Math.hypot(p.x - path[i].x, p.z - path[i].z), 0),
};
const lDistance = (a: WorldPoint, b: WorldPoint) => Math.abs(a.x - b.x) + Math.abs(a.z - b.z);

const near = (p: WorldPoint, dx = 1): WorldPoint => ({ x: p.x + dx, z: p.z });
const far: WorldPoint = { x: -500, z: -500 };

/** A mission system, continued from a `serialize()` text when `saved` is given. */
function harness(saved: string | null = null) {
  const missions = new MissionSystem({ router: lRouter });
  if (saved !== null) missions.restore(saved);
  const states: MissionHudState[] = [];
  missions.subscribe(s => states.push(s));
  const frame = (position: WorldPoint, extra: Partial<MissionFrame> = {}): MissionFrame =>
    ({ position, interact: false, input: 'keyboard', vehicleId: null, ...extra });
  const at = (position: WorldPoint, dt = 0.1, extra: Partial<MissionFrame> = {}) => missions.update(dt, frame(position, extra));
  const press = (position: WorldPoint, extra: Partial<MissionFrame> = {}) => at(position, 0.1, { ...extra, interact: true });
  /** Lets the dialogue queue drain without moving. */
  const wait = (position: WorldPoint, seconds: number) => { for (let t = 0; t < seconds; t += 0.5) at(position, 0.5); };
  /** Selects a menu option in the shop at `point`, opening the shop first when it is closed. */
  const choose = (point: WorldPoint, id: OptionId, extra: Partial<MissionFrame> = {}) => {
    if (!missions.shop) press(near(point), extra);
    return at(near(point), 0.1, { ...extra, choose: id });
  };
  const leave = (point: WorldPoint) => at(near(point), 0.1, { leave: true });
  const option = (id: OptionId) => missions.shop!.options.find(o => o.id === id)!;
  /** Rents a permit (start money) and runs deliveries until `money` is reached. */
  const earn = (money: number) => {
    while (missions.money < money) {
      if (missions.permit === 0) { choose(MARKET.point, 'rent_permit'); leave(MARKET.point); }
      choose(PIZZERIA.point, 'take_order');
      press(near(missions.currentStop.point));
    }
  };
  return { missions, states, at, press, wait, choose, leave, option, earn, last: () => states[states.length - 1] };
}

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
  it('derives par time from route distance and the vehicle class', () => {
    expect(parSeconds(140, 'none')).toBeCloseTo(100 + PAR_HANDLING_SECONDS);
    expect(parSeconds(700, 'scooter')).toBeCloseTo(100 + PAR_HANDLING_SECONDS);
    expect(parSeconds(900, 'car')).toBeCloseTo(100 + PAR_HANDLING_SECONDS);
  });
  it('buys vehicles back at half price', () => {
    expect(sellBackPrice('scooter')).toBe(30);
    expect(sellBackPrice('car')).toBe(175);
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
    for (let n = 0; n <= PERMIT_WARN_AT; n++) expect(dialogueLine(`permit_left_${n}`).voiced).toBe(false);
  });
  it('gives every menu option, reason, zone label and prompt an unvoiced line, and voices the shopkeepers', () => {
    const unvoiced: string[] = Object.values(ZONE_LABELS);
    for (const shop of Object.values(SHOPS)) {
      for (const input of ['pad', 'key']) unvoiced.push(`prompt_enter_${shop.id}_${input}`);
      for (const o of shop.options) unvoiced.push(optionLabel(o.id), ...(optionDetail(o.id) ? [optionDetail(o.id)!] : []));
    }
    unvoiced.push(...DIALOGUE.lines.filter(l => l.id.startsWith('reason_')).map(l => l.id));
    for (const id of unvoiced) expect(dialogueLine(id).voiced, id).toBe(false);
    for (const line of DIALOGUE.lines.filter(l => ['market', 'pizzeria', 'dealer', 'customer'].includes(l.speaker))) {
      expect(line.voiced, line.id).not.toBe(false);
    }
  });
  it('quotes the economy in the lines', () => {
    expect(lineText('intro_3')).toContain(`${START_MONEY} euros`);
    expect(lineText('detail_rent_permit')).toContain(`${PERMIT_DELIVERIES} delivery orders`);
    expect(lineText('reason_permit_stock')).toContain(`${PERMIT_MAX} deliveries`);
    expect(lineText('detail_sell_kidney')).toContain(`${Math.round((1 - KIDNEY_RUN_FACTOR) * 100)} %`);
    expect(lineText('detail_slice')).toContain(`${SLICE_SECONDS / 60} minutes`);
    expect(lineText('detail_expiry_warning')).toContain(`${PERMIT_WARN_AT} deliveries`);
    expect(lineText('pizzeria_tip_rules')).toContain(`${MAX_TIP} euros`);
  });
  it('places every delivery at a distinct street distance', () => {
    const metres = DELIVERIES.map(d => lDistance(PIZZERIA.point, d.point));
    expect([...metres].sort((a, b) => a - b)).toEqual(metres);
  });
});

describe('mission state machine', () => {
  it('starts idle and asks for a permit after begin()', () => {
    const h = harness();
    expect(h.missions.phase).toBe('idle');
    expect(h.last()).toMatchObject({ activeTargetId: null, money: START_MONEY, timerSeconds: null, message: null });
    h.missions.begin();
    h.at(far);
    expect(h.missions.phase).toBe('permit_needed');
    expect(h.last()).toMatchObject({ activeTargetId: MARKET.id, message: lineText('hint_permit') });
    expect(h.last().targets.map(t => t.id)).toEqual([PIZZERIA.id, DEALER.id, MARKET.id]);
  });

  it('opens the black market menu in its zone and rents a permit inside it', () => {
    const h = harness();
    h.missions.begin();
    h.at(near(MARKET.point));
    expect(h.last().message).toBe(lineText('prompt_enter_market_key'));
    h.at(near(MARKET.point), 0.1, { input: 'gamepad' });
    expect(h.last().message).toBe(lineText('prompt_enter_market_pad'));
    expect(h.press(near(MARKET.point)).opened).toBe(true);
    const shop = h.missions.shop!;
    expect(shop).toMatchObject({ id: 'market', title: lineText('zone_market'), line: lineText('market_greet'),
      background: 'bg_market', money: START_MONEY, permit: 0 });
    expect(h.last().message).toBe(lineText('market_greet'));
    expect(shop.options.map(o => [o.id, o.price, o.enabled, o.reason])).toEqual([
      ['rent_permit', PERMIT_PRICE, true, null],
      ['forged_plates', PLATES_PRICE, true, null],
      ['sell_kidney', -KIDNEY_PAYOUT, true, null],
      ['expiry_warning', WARNING_PRICE, true, null],
      ['leave', null, true, null],
    ]);
    expect(shop.options[0]).toMatchObject({ label: lineText('option_rent_permit'), detail: lineText('detail_rent_permit'), icon: 'icon_permit' });

    expect(h.choose(MARKET.point, 'rent_permit').opened).toBe(false);
    expect(h.missions).toMatchObject({ permit: PERMIT_DELIVERIES, money: START_MONEY - PERMIT_PRICE, phase: 'at_pizzeria' });
    expect(h.missions.shop).toMatchObject({ line: lineText('market_sold'), permit: PERMIT_DELIVERIES });
    expect(h.last()).toMatchObject({ message: lineText('market_sold'), activeTargetId: PIZZERIA.id });
    // 40 € left: a second rental is greyed for money; selecting it makes the seller refuse.
    expect(h.option('rent_permit')).toMatchObject({ enabled: false, reason: lineText('reason_no_money') });
    h.choose(MARKET.point, 'rent_permit');
    expect(h.missions).toMatchObject({ permit: PERMIT_DELIVERIES, money: START_MONEY - PERMIT_PRICE });
    expect(h.missions.shop!.line).toBe(lineText('market_no_money'));
    // The interaction button does nothing while the menu is open; Leave closes it and the prompt returns.
    h.press(near(MARKET.point));
    expect(h.missions.shop).not.toBeNull();
    h.choose(MARKET.point, 'leave');
    expect(h.missions.shop).toBeNull();
    h.wait(near(MARKET.point), 8);
    expect(h.last().message).toBe(lineText('prompt_enter_market_key'));
    expect(h.last().targets.map(t => t.id)).toContain(MARKET.id);
  });

  it('closes a menu on leave or when the player is moved away', () => {
    const h = harness();
    h.missions.begin();
    h.press(near(MARKET.point));
    h.leave(MARKET.point);
    expect(h.missions.shop).toBeNull();
    h.press(near(MARKET.point));
    h.at(near(MARKET.point, 7));
    expect(h.missions.shop).toBeNull();
  });

  it('quotes the order at the pizzeria, then times and pays the delivery at a drop-off without a menu', () => {
    const h = harness();
    h.missions.begin();
    h.choose(MARKET.point, 'rent_permit');
    h.leave(MARKET.point);
    h.wait(far, 15);
    expect(h.last().message).toBe(lineText('hint_pizzeria'));

    h.press(near(PIZZERIA.point));
    const d = DELIVERIES[0];
    const metres = lDistance(PIZZERIA.point, d.point);
    const par = parSeconds(metres, 'none');
    expect(h.missions.shop).toMatchObject({ id: 'pizzeria', line: lineText('pizzeria_first') });
    expect(h.option('take_order')).toMatchObject({ enabled: true, price: null, quote: { address: d.address, metres, par } });
    expect(h.option('slice')).toMatchObject({ enabled: true, price: SLICE_PRICE });
    h.choose(PIZZERIA.point, 'tip_rules');
    expect(h.missions.shop!.line).toBe(lineText('pizzeria_tip_rules'));
    h.choose(PIZZERIA.point, 'take_order');
    expect(h.missions.shop).toBeNull();
    expect(h.missions).toMatchObject({ phase: 'carrying', permit: PERMIT_DELIVERIES - 1 });
    expect(h.last()).toMatchObject({ activeTargetId: d.id, message: lineText(d.handover), timerSeconds: Math.ceil(2 * par) });
    expect(h.last().targets.find(t => t.id === d.id)).toMatchObject({ kind: 'dropoff', point: d.point });
    h.wait(far, 15);
    expect(h.last().message).toBe(lineText(d.hint));

    // The pizzeria will not hand out a second order meanwhile.
    h.press(near(PIZZERIA.point));
    expect(h.missions.shop!.line).toBe(lineText('pizzeria_carrying'));
    expect(h.option('take_order')).toMatchObject({ enabled: false, reason: lineText('reason_carrying') });
    h.leave(PIZZERIA.point);

    h.wait(near(d.point, INTERACT_RADIUS - 0.1), 8);
    expect(h.last().message).toBe(lineText('prompt_hand_over_key'));
    const elapsed = h.missions.snapshot().elapsed;
    expect(h.press(near(d.point)).opened).toBe(false);
    const expectedTip = tip(elapsed + 0.1, par);
    expect(expectedTip).toBe(MAX_TIP);
    expect(h.missions).toMatchObject({ phase: 'delivered', shop: null });
    expect(h.last()).toMatchObject({ money: START_MONEY - PERMIT_PRICE + BASE_PAY + expectedTip, timerSeconds: null,
      message: lineText(d.customer.fast), activeTargetId: PIZZERIA.id });
    expect(h.last().targets.map(t => t.id)).not.toContain(d.id);
    h.wait(near(d.point), lineSeconds(lineText(d.customer.fast)) + 0.1);
    expect(h.last().message).toBe(lineText(`paid_tip_${expectedTip}`));
    h.wait(far, 15);
    expect(h.missions.phase).toBe('at_pizzeria');
    expect(h.missions.currentStop.scripted).toBe(DELIVERIES[1]);
    h.press(near(PIZZERIA.point));
    expect(h.missions.shop!.line).toBe(lineText('pizzeria_back'));
  });

  it('does nothing outside the interaction radius', () => {
    const h = harness();
    h.missions.begin();
    h.press(near(MARKET.point, INTERACT_RADIUS + 0.2));
    expect(h.missions.shop).toBeNull();
  });

  it('uses one permit delivery per order and stops orders when it runs out', () => {
    const h = harness();
    h.missions.begin();
    h.choose(MARKET.point, 'rent_permit');
    h.leave(MARKET.point);
    for (let i = 0; i < PERMIT_DELIVERIES; i++) {
      h.choose(PIZZERIA.point, 'take_order');
      h.press(near(h.missions.currentStop.point));
    }
    h.wait(far, 15);
    expect(h.missions).toMatchObject({ permit: 0, phase: 'permit_needed' });
    expect(h.last()).toMatchObject({ activeTargetId: MARKET.id, message: lineText('hint_permit') });
    h.press(near(PIZZERIA.point));
    expect(h.missions.shop!.line).toBe(lineText('pizzeria_no_permit'));
    expect(h.option('take_order')).toMatchObject({ enabled: false, reason: lineText('reason_no_permit') });
    h.choose(PIZZERIA.point, 'take_order');
    expect(h.missions.phase).toBe('permit_needed');
    h.leave(PIZZERIA.point);
    h.press(near(MARKET.point));
    expect(h.missions.shop!.line).toBe(lineText('market_greet_expired'));
    h.choose(MARKET.point, 'rent_permit');
    expect(h.missions).toMatchObject({ permit: PERMIT_DELIVERIES, phase: 'at_pizzeria' });
  });

  it('rents at most PERMIT_MAX deliveries ahead', () => {
    const h = harness();
    h.missions.begin();
    h.choose(MARKET.point, 'sell_kidney');
    h.choose(MARKET.point, 'rent_permit');
    h.choose(MARKET.point, 'rent_permit');
    expect(h.missions.permit).toBe(PERMIT_MAX);
    expect(h.option('rent_permit')).toMatchObject({ enabled: false, reason: lineText('reason_permit_stock') });
    h.choose(MARKET.point, 'rent_permit');
    expect(h.missions.permit).toBe(PERMIT_MAX);
  });

  it('speaks the bought-back expiry warning after hand-overs near the end of the permit', () => {
    const h = harness();
    h.missions.begin();
    h.choose(MARKET.point, 'rent_permit');
    h.choose(MARKET.point, 'expiry_warning');
    expect(h.missions.money).toBe(START_MONEY - PERMIT_PRICE - WARNING_PRICE);
    expect(h.option('expiry_warning')).toMatchObject({ enabled: false, reason: lineText('reason_warning_owned') });
    h.leave(MARKET.point);
    const lines: string[][] = [];
    for (let i = 0; i < PERMIT_DELIVERIES; i++) {
      h.choose(PIZZERIA.point, 'take_order');
      h.press(near(h.missions.currentStop.point));
      lines.push(h.missions.snapshot().dialogue);
    }
    expect(lines.slice(0, PERMIT_DELIVERIES - PERMIT_WARN_AT - 1).every(l => l.length === 2)).toBe(true);
    expect(lines.slice(-3).map(l => l[2])).toEqual(['permit_left_2', 'permit_left_1', 'permit_left_0']);
  });

  it('fails a delivery after twice the par time and moves on to the next address', () => {
    const h = harness();
    h.missions.begin();
    h.choose(MARKET.point, 'rent_permit');
    h.leave(MARKET.point);
    h.choose(PIZZERIA.point, 'take_order');
    const par = parSeconds(lDistance(PIZZERIA.point, DELIVERIES[0].point), 'none');
    h.wait(far, 2 * par + 1);
    expect(h.missions.phase === 'failed' || h.missions.phase === 'at_pizzeria').toBe(true);
    expect(h.last().money).toBe(START_MONEY - PERMIT_PRICE);
    expect(h.missions.currentStop.scripted).toBe(DELIVERIES[1]);
    h.wait(far, 15);
    h.press(near(PIZZERIA.point));
    expect(h.missions.shop!.line).toBe(lineText('pizzeria_after_fail'));
  });

  it('pays no tip for a slow but completed delivery', () => {
    const h = harness();
    h.missions.begin();
    h.choose(MARKET.point, 'rent_permit');
    h.leave(MARKET.point);
    h.choose(PIZZERIA.point, 'take_order');
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
    h.choose(MARKET.point, 'rent_permit');
    h.leave(MARKET.point);
    const order: string[] = [];
    for (let i = 0; i < DELIVERIES.length + 2; i++) {
      h.choose(PIZZERIA.point, 'take_order');
      order.push(h.missions.currentStop.id);
      h.press(near(h.missions.currentStop.point));
    }
    expect(order).toEqual([...DELIVERIES, DELIVERIES[0], DELIVERIES[1]].map(d => d.id));
  });

  it('sells the kidney once for a permanent run penalty that a slice lifts for a while', () => {
    const h = harness();
    h.missions.begin();
    expect(h.missions.runFactor).toBe(1);
    h.choose(MARKET.point, 'sell_kidney');
    expect(h.missions).toMatchObject({ kidney: true, money: START_MONEY + KIDNEY_PAYOUT, runFactor: KIDNEY_RUN_FACTOR });
    expect(h.missions.shop!.line).toBe(lineText('market_sold_kidney'));
    expect(h.option('sell_kidney')).toMatchObject({ enabled: false, reason: lineText('reason_kidney_sold') });
    h.choose(MARKET.point, 'sell_kidney');
    expect(h.missions.money).toBe(START_MONEY + KIDNEY_PAYOUT);
    h.leave(MARKET.point);
    const money = h.missions.money;
    h.choose(PIZZERIA.point, 'slice');
    expect(h.missions).toMatchObject({ money: money - SLICE_PRICE, runFactor: 1 });
    expect(h.missions.shop!.line).toBe(lineText('pizzeria_slice'));
    h.leave(PIZZERIA.point);
    h.wait(far, SLICE_SECONDS - 1);
    expect(h.missions.runFactor).toBe(1);
    h.wait(far, 2);
    expect(h.missions.runFactor).toBe(KIDNEY_RUN_FACTOR);
  });

  it('sells vehicles one at a time after the permit, and buys them back at half price', () => {
    const h = harness();
    h.missions.begin();
    h.press(near(DEALER.point));
    expect(h.missions.shop).toMatchObject({ id: 'dealer', line: lineText('dealer_no_permit') });
    expect(h.missions.shop!.options.filter(o => o.id !== 'leave').every(o => o.reason === lineText('reason_no_permit'))).toBe(true);
    expect(h.choose(DEALER.point, 'buy_scooter').bought).toBeNull();
    h.leave(DEALER.point);
    h.choose(MARKET.point, 'rent_permit'); // 40 € left
    h.leave(MARKET.point);
    h.press(near(DEALER.point));
    expect(h.missions.shop!.line).toBe(lineText('dealer_greet'));
    expect(h.option('buy_scooter')).toMatchObject({ enabled: false, reason: lineText('reason_no_money'), price: VEHICLE_PRICE.scooter });
    expect(h.option('sell_vehicle')).toMatchObject({ enabled: false, reason: lineText('reason_no_vehicle') });
    h.choose(DEALER.point, 'buy_scooter');
    expect(h.missions.shop!.line).toBe(lineText('dealer_no_money'));
    h.leave(DEALER.point);
    h.earn(VEHICLE_PRICE.scooter);
    const money = h.missions.money;
    expect(h.choose(DEALER.point, 'buy_scooter').bought).toBe('scooter');
    expect(h.missions).toMatchObject({ vehicle: 'scooter', money: money - VEHICLE_PRICE.scooter, shop: null });
    expect(h.last().message).toBe(lineText('dealer_sold_scooter'));
    h.press(near(DEALER.point));
    expect(h.option('buy_car')).toMatchObject({ enabled: false, reason: lineText('reason_vehicle_owned') });
    expect(h.option('sell_vehicle')).toMatchObject({ enabled: true, price: -sellBackPrice('scooter') });
    h.choose(DEALER.point, 'sell_vehicle');
    expect(h.missions).toMatchObject({ vehicle: 'none', money: money - VEHICLE_PRICE.scooter + sellBackPrice('scooter') });
    expect(h.missions.shop!.line).toBe(lineText('dealer_bought_back'));
  });

  it('uses the owned vehicle for par time and allows only that vehicle while carrying', () => {
    const h = harness();
    h.missions.begin();
    h.choose(MARKET.point, 'rent_permit');
    h.leave(MARKET.point);
    h.earn(VEHICLE_PRICE.scooter);
    expect(h.choose(DEALER.point, 'buy_scooter').bought).toBe('scooter');
    expect(ownedVehicleId('scooter')).toBe(SCOOTER_ID);
    expect(h.missions.mayDrive('parked-1')).toBe(true); // free roam: any car
    h.press(near(PIZZERIA.point), { vehicleId: 'parked-1' });
    expect(h.option('take_order')).toMatchObject({ enabled: false, reason: lineText('reason_own_vehicle_only') });
    h.choose(PIZZERIA.point, 'take_order', { vehicleId: 'parked-1' });
    expect(h.missions.phase).not.toBe('carrying');
    // With a scooter owned, the start car is not the player's vehicle.
    h.choose(PIZZERIA.point, 'take_order', { vehicleId: OWN_VEHICLE_ID });
    expect(h.missions.phase).not.toBe('carrying');
    h.choose(PIZZERIA.point, 'take_order', { vehicleId: SCOOTER_ID });
    expect(h.missions.phase).toBe('carrying');
    const d = h.missions.currentStop;
    expect(h.missions.snapshot().par).toBeCloseTo(parSeconds(lDistance(PIZZERIA.point, d.point), 'scooter'));
    expect(h.missions.mayDrive(SCOOTER_ID)).toBe(true);
    expect(h.missions.mayDrive(OWN_VEHICLE_ID)).toBe(false);
    expect(h.missions.mayDrive('parked-1')).toBe(false);
    expect(h.last().message).toBe(lineText('notice_own_vehicle_only'));
  });

  it('lets forged plates deliver with any car, at car par time', () => {
    const h = harness();
    h.missions.begin();
    h.choose(MARKET.point, 'rent_permit');
    h.choose(MARKET.point, 'sell_kidney');
    h.choose(MARKET.point, 'forged_plates');
    expect(h.missions).toMatchObject({ plates: true, money: START_MONEY - PERMIT_PRICE + KIDNEY_PAYOUT - PLATES_PRICE });
    expect(h.option('forged_plates')).toMatchObject({ enabled: false, reason: lineText('reason_plates_owned') });
    h.leave(MARKET.point);
    h.choose(PIZZERIA.point, 'take_order', { vehicleId: 'parked-1' });
    expect(h.missions.phase).toBe('carrying');
    const d = h.missions.currentStop;
    expect(h.missions.snapshot().par).toBeCloseTo(parSeconds(lDistance(PIZZERIA.point, d.point), 'car'));
    expect(h.missions.mayDrive('parked-2')).toBe(true);
  });

  it('continues a saved game after a reload, including the running timer', () => {
    const a = harness();
    a.missions.begin();
    a.choose(MARKET.point, 'rent_permit');
    a.choose(MARKET.point, 'expiry_warning');
    a.choose(MARKET.point, 'leave');
    a.choose(PIZZERIA.point, 'take_order');
    a.wait(far, 10);
    const saved = a.missions.serialize();
    expect(JSON.parse(saved)).toMatchObject({ version: 3, phase: 'carrying', permit: PERMIT_DELIVERIES - 1, warning: true,
      money: START_MONEY - PERMIT_PRICE - WARNING_PRICE });
    const b = harness(saved);
    expect(b.missions).toMatchObject({ phase: 'carrying', permit: PERMIT_DELIVERIES - 1 });
    b.missions.begin();
    b.at(far, 0);
    expect(b.missions.phase).toBe('carrying');
    expect(b.last().timerSeconds).toBe(a.last().timerSeconds);
    expect(b.last().activeTargetId).toBe(DELIVERIES[0].id);
  });

  it('restores over a running game: dialogue and the open shop are dropped, null starts a new game', () => {
    const h = harness();
    h.missions.begin();
    h.choose(MARKET.point, 'rent_permit');
    const rented = h.missions.serialize();
    h.choose(MARKET.point, 'sell_kidney');
    expect(h.missions.shop?.id).toBe('market');
    h.missions.restore(rented);
    expect(h.missions).toMatchObject({ phase: 'at_pizzeria', permit: PERMIT_DELIVERIES, kidney: false, shop: null,
      money: START_MONEY - PERMIT_PRICE });
    expect(h.missions.snapshot().dialogue).toEqual([]);
    expect(h.last().money).toBe(START_MONEY - PERMIT_PRICE);
    h.missions.restore(null);
    expect(h.missions).toMatchObject({ phase: 'idle', permit: 0, money: START_MONEY, deliveries: 0, lastAddress: null });
    h.missions.begin();
    expect(h.missions.phase).toBe('permit_needed');
  });

  it('reports a checkpoint after each purchase and each finished delivery, and nothing else', () => {
    const h = harness();
    h.missions.begin();
    const checkpoints: (string | null)[] = [];
    const record = (r: { checkpoint: string | null }) => { checkpoints.push(r.checkpoint); return r; };
    record(h.press(near(MARKET.point)));
    record(h.at(near(MARKET.point), 0.1, { choose: 'rent_permit' }));
    record(h.at(near(MARKET.point), 0.1, { choose: 'forged_plates' })); // not enough money: no purchase
    record(h.leave(MARKET.point));
    expect(checkpoints).toEqual([null, 'purchase', null, null]);
    h.choose(PIZZERIA.point, 'take_order');
    const deliveryAddress = h.missions.currentStop.address;
    expect(record(h.press(near(h.missions.currentStop.point))).checkpoint).toBe('delivery');
    expect(h.missions).toMatchObject({ deliveries: 1, lastAddress: deliveryAddress });
    expect(record(h.at(far, 0.1)).checkpoint).toBeNull();
    h.choose(PIZZERIA.point, 'take_order');
    let failed: string | null = null;
    for (let t = 0; t < 2000 && failed === null; t += 1) failed = h.at(far, 1).checkpoint;
    expect(failed).toBe('delivery');
    expect(h.missions.deliveries).toBe(2);
  });

  it('summarises a save text for the slot list', () => {
    const h = harness();
    expect(missionSummary(h.missions.serialize())).toEqual({ deliveries: 0, money: START_MONEY, lastAddress: null });
    h.missions.begin();
    h.earn(START_MONEY + 1);
    expect(missionSummary(h.missions.serialize())).toEqual({ deliveries: h.missions.deliveries, money: h.missions.money,
      lastAddress: h.missions.lastAddress });
    expect(missionSummary('not json')).toEqual({ deliveries: 0, money: START_MONEY, lastAddress: null });
  });

  it('migrates an owned bicycle in any save version to the scooter', () => {
    const v2 = { version: 2, phase: 'at_pizzeria', money: 40, permit: 3, plates: false, kidney: false, warning: false,
      vehicle: 'bicycle', delivery: 4, elapsed: 0, par: 0 };
    const h = harness(JSON.stringify(v2));
    expect(h.missions).toMatchObject({ vehicle: 'scooter', money: 40, permit: 3 });
    const v3 = JSON.parse(h.missions.serialize());
    expect(v3).toMatchObject({ version: 3, vehicle: 'scooter' });
    expect(harness(JSON.stringify({ ...v3, vehicle: 'bicycle' })).missions.vehicle).toBe('scooter');
    expect(harness(JSON.stringify({ ...v2, vehicle: 'unicycle' })).missions).toMatchObject({ vehicle: 'none', money: START_MONEY });
  });

  it('migrates a save from main 2673ec0: an owned licence becomes a fresh permit rental', () => {
    // Byte-for-byte what MissionSystem.persist() of 2673ec0 wrote.
    const v1 = (fields: string) => `{"version":1,${fields},"vehicle":"bicycle","delivery":5,"elapsed":12.5,"par":140}`;
    const h = harness(v1('"phase":"carrying","money":57,"licence":true'));
    expect(h.missions).toMatchObject({ phase: 'carrying', money: 57, permit: PERMIT_DELIVERIES, vehicle: 'scooter',
      plates: false, kidney: false, currentStop: { scripted: DELIVERIES[1] } });
    expect(h.last().timerSeconds).toBe(Math.ceil(2 * 140 - 12.5));
    h.press(near(DELIVERIES[1].point));
    expect(JSON.parse(h.missions.serialize())).toMatchObject({ version: 3, permit: PERMIT_DELIVERIES, delivery: 6 });

    expect(harness(v1('"phase":"licence_needed","money":120,"licence":false')).missions)
      .toMatchObject({ phase: 'permit_needed', permit: 0, money: 120 });
    expect(harness(v1('"phase":"delivered","money":80,"licence":true')).missions)
      .toMatchObject({ phase: 'at_pizzeria', permit: PERMIT_DELIVERIES });
  });

  it('falls back to a new game on a corrupt save', () => {
    expect(harness('{"version":1,"phase":"carrying","money":-5}').missions).toMatchObject({ phase: 'idle', money: START_MONEY });
    expect(harness('{"version":2,"phase":"carrying","money":5,"permit":"many"}').missions.phase).toBe('idle');
    expect(harness('not json').missions.phase).toBe('idle');
    expect(harness('null').missions.phase).toBe('idle');
  });

  it('notifies subscribers only on change and stops after unsubscribe', () => {
    const missions = new MissionSystem({ router: lRouter });
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
    expect(missions.shop?.id).toBe('market');
  });

  it('keeps the menu view object while nothing in it changes', () => {
    const h = harness();
    h.missions.begin();
    h.press(near(MARKET.point));
    const view = h.missions.shop;
    h.at(near(MARKET.point));
    expect(h.missions.shop).toBe(view);
    h.choose(MARKET.point, 'rent_permit');
    expect(h.missions.shop).not.toBe(view);
  });
});
