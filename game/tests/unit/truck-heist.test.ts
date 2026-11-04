// The black market's truck heist (docs/design/story.md, "Truck heist").
import { describe, expect, it } from 'vitest';
import { lineText } from '../../src/gameplay/content/dialogue';
import { HEIST_DROP, HEIST_YARD, MARKET, PIZZERIA } from '../../src/gameplay/content/locations';
import { SHOPS } from '../../src/gameplay/content/shops';
import { HEIST_CASH } from '../../src/gameplay/economy';
import { HEIST_DROP_RADIUS, TRUCK_ID } from '../../src/gameplay/missions/missions';
import { freshSave } from '../../src/gameplay/missions/save';
import { far, harness, near } from './missionHarness';

const drop = HEIST_DROP.point;
const under = { x: drop.x + 2, y: drop.y, z: drop.z };
const onDeck = { x: drop.x, y: drop.y! + 5.8, z: drop.z };
/** A game at the pizzeria with a permit and the opening done. */
const ready = (extra: Record<string, unknown> = {}) => harness(JSON.stringify({ ...freshSave(1), phase: 'at_pizzeria',
  permit: 5, delivery: 4, shortDeliveries: 4, money: 200, ...extra }));

describe('truck heist', () => {
  it('is offered at the black market unless the truck is owned, the job runs or a pizza is carried', () => {
    expect(SHOPS.market.options.map(o => o.id)).toEqual(expect.arrayContaining(['job_truck_heist', 'abandon_truck_heist']));
    const h = ready();
    h.press(near(MARKET.point));
    expect(h.option('job_truck_heist')).toMatchObject({ enabled: true, price: null });
    expect(h.option('abandon_truck_heist')).toMatchObject({ enabled: false, reason: lineText('reason_no_heist') });
    h.leave(MARKET.point);
    const owned = ready({ vehicle: 'truck' });
    owned.press(near(MARKET.point));
    expect(owned.option('job_truck_heist')).toMatchObject({ enabled: false, reason: lineText('reason_heist_truck_owned') });
    // Bought plates: the job pays cash instead, and says so.
    const plated = ready({ plates: true });
    plated.press(near(MARKET.point));
    expect(plated.option('job_truck_heist')).toMatchObject({ enabled: true, reason: null, detail: lineText('detail_job_truck_heist_cash') });
    // A running job is not offered again; its own row says it runs, not the plates' text.
    const running = ready();
    running.choose(MARKET.point, 'job_truck_heist');
    expect(running.option('job_truck_heist')).toMatchObject({ enabled: false, reason: lineText('reason_heist_running') });
    expect(running.option('abandon_truck_heist')).toMatchObject({ enabled: true });
    const c = ready();
    c.choose(PIZZERIA.point, 'take_order');
    expect(c.missions.phase).toBe('carrying');
    c.leave(PIZZERIA.point);
    c.press(near(MARKET.point));
    expect(c.option('job_truck_heist')).toMatchObject({ enabled: false, reason: lineText('reason_carrying') });
  });

  it('runs from the yard to under the bridge: plates on, the truck goes, orders open again', () => {
    const h = ready();
    expect(h.choose(MARKET.point, 'job_truck_heist').heist).toBe('started');
    expect(h.missions.heist).toBe('pickup');
    expect(h.missions.shop!.line).toBe(lineText('heist_briefing'));
    h.leave(MARKET.point);
    expect(h.last()).toMatchObject({ activeTargetId: HEIST_YARD.id });
    expect(h.last().targets.find(t => t.id === HEIST_YARD.id)).toMatchObject({ kind: 'job' });
    // Pizza orders wait until the job is done.
    h.press(near(PIZZERIA.point));
    expect(h.option('take_order')).toMatchObject({ enabled: false, reason: lineText('reason_heist_active') });
    h.leave(PIZZERIA.point);
    h.wait(far, 20);
    expect(h.last().message).toBe(lineText('hint_heist_pickup'));
    // Getting into the truck sends it to the bridge.
    h.at(HEIST_YARD.point, 0.1, { vehicleId: TRUCK_ID });
    expect(h.missions.heist).toBe('deliver');
    expect(h.missions.snapshot().dialogue).toEqual(['heist_in_truck']);
    expect(h.last().activeTargetId).toBe(HEIST_DROP.id);
    expect(h.last().targets.find(t => t.id === HEIST_DROP.id)!.approach).toBeDefined();
    // Getting out elsewhere, on the bridge deck above, or out of another car under it does not count.
    h.at(far, 0.1, { vehicleId: TRUCK_ID }); h.at(far);
    h.at(onDeck, 0.1, { vehicleId: TRUCK_ID }); h.at(onDeck);
    h.at(under, 0.1, { vehicleId: 'player' }); h.at(under);
    expect(h.missions.heist).toBe('deliver');
    expect(h.missions.plates).toBe(false);
    h.at(under, 0.1, { vehicleId: TRUCK_ID });
    const done = h.at({ ...under, x: drop.x + HEIST_DROP_RADIUS - 1 });
    expect(done).toMatchObject({ heist: 'delivered', checkpoint: 'purchase' });
    expect(h.missions).toMatchObject({ heist: null, plates: true, vehicle: 'none' });
    expect(h.missions.snapshot().dialogue).toEqual(['heist_done']);
    expect(h.last().targets.some(t => t.kind === 'job')).toBe(false);
    h.wait(far, 15);
    h.choose(PIZZERIA.point, 'take_order', { vehicleId: 'parked-7' });
    expect(h.missions.phase).toBe('carrying'); // the plates let any car deliver
  });

  it('is offered again after each run: a fresh yard target, the truck back, cash once per run', () => {
    const h = ready();
    const run = () => {
      expect(h.choose(MARKET.point, 'job_truck_heist').heist).toBe('started');
      h.leave(MARKET.point);
      expect(h.last()).toMatchObject({ activeTargetId: HEIST_YARD.id });
      h.at(HEIST_YARD.point, 0.1, { vehicleId: TRUCK_ID });
      expect(h.missions.heist).toBe('deliver');
      h.at(under, 0.1, { vehicleId: TRUCK_ID });
      return h.at(under);
    };
    expect(run().heist).toBe('delivered');
    expect(h.missions).toMatchObject({ heist: null, plates: true, money: 200 });
    // Offered again, now for cash.
    h.wait(far, 1);
    h.press(near(MARKET.point));
    expect(h.option('job_truck_heist')).toMatchObject({ enabled: true, reason: null, detail: lineText('detail_job_truck_heist_cash') });
    h.leave(MARKET.point);
    expect(run().heist).toBe('delivered');
    expect(h.missions.shop).toBeNull();
    expect(h.missions).toMatchObject({ heist: null, plates: true, money: 200 + HEIST_CASH });
    expect(h.missions.snapshot().dialogue).toEqual(['heist_done_cash']);
    expect(h.last().targets.some(t => t.kind === 'job')).toBe(false);
    // Lingering at the drop-off, or getting back in and out, pays nothing more.
    h.wait(under, 5);
    h.at(under, 0.1, { vehicleId: TRUCK_ID }); h.at(under);
    expect(h.missions.money).toBe(200 + HEIST_CASH);
    // Abandoning a repeat run leaves it on offer as well.
    h.choose(MARKET.point, 'job_truck_heist');
    expect(h.missions.shop!.line).toBe(lineText('heist_briefing_cash'));
    expect(h.choose(MARKET.point, 'abandon_truck_heist').heist).toBe('abandoned');
    expect(h.option('job_truck_heist')).toMatchObject({ enabled: true });
    expect(h.missions.money).toBe(200 + HEIST_CASH);
  });

  it('can be abandoned at the market, out of the truck, and started again', () => {
    const h = ready();
    h.choose(MARKET.point, 'job_truck_heist');
    h.at(near(MARKET.point), 0.1, { vehicleId: TRUCK_ID });
    expect(h.missions.heist).toBe('deliver');
    h.press(near(MARKET.point), { vehicleId: TRUCK_ID });
    expect(h.option('abandon_truck_heist')).toMatchObject({ enabled: false, reason: lineText('reason_heist_in_truck') });
    h.leave(MARKET.point);
    h.at(near(MARKET.point));
    expect(h.choose(MARKET.point, 'abandon_truck_heist').heist).toBe('abandoned');
    expect(h.missions).toMatchObject({ heist: null, plates: false });
    expect(h.missions.shop!.line).toBe(lineText('heist_abandoned'));
    expect(h.option('job_truck_heist').enabled).toBe(true);
    h.leave(MARKET.point);
    h.choose(PIZZERIA.point, 'take_order');
    expect(h.missions.phase).toBe('carrying');
  });

  it('saves a running job and resumes it with the truck back in the yard', () => {
    const h = ready();
    h.choose(MARKET.point, 'job_truck_heist');
    h.leave(MARKET.point);
    expect(JSON.parse(h.missions.serialize())).toMatchObject({ heist: 'pickup' });
    h.at(HEIST_YARD.point, 0.1, { vehicleId: TRUCK_ID });
    const saved = h.missions.serialize();
    expect(JSON.parse(saved)).toMatchObject({ heist: 'deliver' });
    const b = harness(saved);
    expect(b.missions.heist).toBe('pickup');
    b.at(HEIST_YARD.point, 0.1, { vehicleId: TRUCK_ID });
    b.at(under, 0.1, { vehicleId: TRUCK_ID });
    expect(b.at(under).heist).toBe('delivered');
    expect(harness(b.missions.serialize()).missions).toMatchObject({ heist: null, plates: true });
    // Saves from before the heist have no field: no job.
    const { heist: _, ...old } = JSON.parse(saved);
    expect(harness(JSON.stringify(old)).missions.heist).toBeNull();
  });
});
