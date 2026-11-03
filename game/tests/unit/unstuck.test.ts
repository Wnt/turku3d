// Getting un-stuck in the delivery career (docs/design/story.md, "Economy"): each dead end is reached, then the player
// starts a vehicle delivery again without running.
import { describe, expect, it } from 'vitest';
import { DEALER, MARKET, PIZZERIA, PIZZERIA_VEHICLE_SPOT } from '../../src/gameplay/content/locations';
import { bringSpots, kerbSpots, offScreen } from '../../src/gameplay/kerbSpots';
import { LOANER_FEE, parSeconds } from '../../src/gameplay/economy';
import { BRING_COOLDOWN_SECONDS, BRING_NEAR_M, LOANER_ID, SCOOTER_ID } from '../../src/gameplay/missions/missions';
import { CAR, FOOT, mergeRoadGraphs, type RoadGraphJson } from '../../src/gameplay/roadGraph';
import { SaveMenuFlow, type GameHooks, type MenuPage } from '../../src/ui/menu';
import { optionDetail } from '../../src/ui/shopMenu';
import { far, harness, near } from './missionHarness';

/** A two-way street (class 2, 6.5 m wide in v1) from (0, 0) to (100, 0) and a junction at its east end. */
function street(): RoadGraphJson {
  const flags = CAR | FOOT | (2 << 3);
  return { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox: [0, -50, 150, 50],
    nodes: [0, 0, 100, 0, 150, 0, 100, 50, 100, -50],
    edges: [[0, 1, flags, 100], [1, 2, flags, 50], [1, 3, flags, 50], [1, 4, flags, 50]] };
}

describe('kerb spots', () => {
  const graph = mergeRoadGraphs([street()]);

  it('parks at both kerbs facing each side\'s traffic, clear of junctions, nearest first', () => {
    const spots = kerbSpots(graph, { x: 50, y: 3, z: 10 }, 1, 30);
    expect(spots.length).toBeGreaterThan(4);
    // Half of 6.5 m minus the half width and the 0.4 m kerb gap.
    const offset = 6.5 / 2 - 1 - 0.4;
    for (const s of spots) {
      expect(Math.abs(Math.abs(s.z) - offset)).toBeLessThan(1e-9);
      // Right-hand traffic: the a→b (+x) travel is on the right of +x, which is +z in engine coordinates.
      expect(Math.cos(s.yaw)).toBeCloseTo(s.z > 0 ? 1 : -1);
      expect(s.y).toBe(3);
    }
    expect(spots[0].z).toBeCloseTo(offset);
    const d = spots.map(s => Math.hypot(s.x - 50, s.z - 10));
    expect(d).toEqual([...d].sort((a, b) => a - b));
    // The junction at x 100 (four streets) keeps 10 m clear on every street that meets there.
    expect(kerbSpots(graph, { x: 100, z: 0 }, 1, 9.5)).toEqual([]);
  });

  it('prefers spots outside the camera view, then the rest, both nearest first', () => {
    const spots = kerbSpots(graph, { x: 50, z: 0 }, 1, 30);
    const view = { camera: { x: 50, z: 0 }, forward: { x: 1, z: 0 }, halfFov: Math.PI / 4 };
    const ranked = bringSpots(spots, { x: 50, z: 0 }, view);
    expect(ranked.every(s => Math.hypot(s.x - 50, s.z) >= 5)).toBe(true);
    const firstOn = ranked.findIndex(s => !offScreen(s, view));
    expect(firstOn).toBeGreaterThan(0);
    expect(ranked.slice(0, firstOn).every(s => offScreen(s, view))).toBe(true);
    expect(ranked.slice(firstOn).every(s => !offScreen(s, view))).toBe(true);
    expect(ranked[0].x).toBeLessThan(50); // behind the camera
  });
});

describe('bring my vehicle', () => {
  /** A game that rented a permit and bought the scooter. */
  function scooterOwner() {
    const h = harness();
    h.missions.begin();
    h.choose(MARKET.point, 'rent_permit'); h.leave(MARKET.point);
    h.missions.grantDevMoney(100);
    h.choose(DEALER.point, 'buy_scooter');
    h.wait(DEALER.point, 10);
    expect(h.missions.vehicle).toBe('scooter');
    return h;
  }

  it('is greyed out at the pizzeria without an own vehicle', () => {
    const h = harness();
    h.missions.begin();
    h.choose(MARKET.point, 'rent_permit'); h.leave(MARKET.point);
    h.press(near(PIZZERIA.point));
    expect(h.option('bring_vehicle')).toMatchObject({ enabled: false, reason: 'You do not own a vehicle.', price: null });
  });

  it('dead end 1: the own scooter is stuck far away, no plates: the pizzeria parks it at its kerb', () => {
    const h = scooterOwner();
    const stuck = far;
    h.press(near(PIZZERIA.point), { rideAt: stuck });
    expect(h.option('bring_vehicle').enabled).toBe(true);
    const result = h.at(near(PIZZERIA.point), 0.1, { choose: 'bring_vehicle', rideAt: stuck });
    expect(result.park).toBe(true);
    expect(h.missions.shop?.line).toBe('My nephew fetched it. It is at the kerb outside, keys in.');
    // The game parks it at PIZZERIA_VEHICLE_SPOT: now it is outside, and the player rides it to the next order.
    h.at(near(PIZZERIA.point), 0.1, { rideAt: PIZZERIA_VEHICLE_SPOT });
    expect(h.option('bring_vehicle')).toMatchObject({ enabled: false, reason: 'It is already outside.' });
    h.leave(PIZZERIA.point);
    h.press(near(PIZZERIA.point), { vehicleId: SCOOTER_ID });
    expect(h.option('take_order')).toMatchObject({ enabled: true, reason: null });
    expect(h.option('bring_vehicle').reason).toBe('You are already riding it.');
    h.at(near(PIZZERIA.point), 0.1, { choose: 'take_order', vehicleId: SCOOTER_ID });
    expect(h.missions.phase).toBe('carrying');
    expect(h.missions.snapshot().par).toBeLessThan(200); // scooter par, not on foot
  });

  it('greys out within BRING_NEAR_M of the pizzeria and does nothing then', () => {
    const h = scooterOwner();
    const close = { x: PIZZERIA.point.x + BRING_NEAR_M - 1, z: PIZZERIA.point.z };
    h.press(near(PIZZERIA.point), { rideAt: close });
    expect(h.at(near(PIZZERIA.point), 0.1, { choose: 'bring_vehicle', rideAt: close }).park).toBe(false);
  });

  it('brings it beside the player from the pause menu, then waits BRING_COOLDOWN_SECONDS of play', () => {
    const h = scooterOwner();
    expect(h.missions.bringHereReason()).toBeNull();
    h.at(far, 0.1, { vehicleId: SCOOTER_ID });
    expect(h.missions.bringHereReason()).toBe('reason_riding_it');
    h.at(far, 0.1);
    h.missions.broughtHere();
    expect(h.last().message).toBe('Your vehicle is parked on the street nearby.');
    expect(h.missions.bringHereReason()).toBe('reason_bring_cooldown');
    h.at(far, 0); // paused: the cooldown waits
    expect(h.missions.bringCooldownLeft).toBe(BRING_COOLDOWN_SECONDS);
    for (let t = 0; t < BRING_COOLDOWN_SECONDS; t += 0.5) h.at(far, 0.5);
    expect(h.missions.bringHereReason()).toBeNull();
    h.missions.bringFailed();
    expect(h.last().message).toBe('No street nearby has room for your vehicle.');
    expect(h.missions.bringHereReason()).toBeNull();
  });

  it('shows the pause menu row with its reason and resumes play when picked', () => {
    let reason: string | null = 'Ready again in 12 s';
    const calls: string[] = [];
    const host = { page: null as MenuPage | null, show(page: MenuPage) { this.page = page; } };
    const hooks: GameHooks = { capture: () => { throw new Error('unused'); }, play: () => {}, resume: () => {}, quit: () => {},
      respawn: () => {}, bringVehicle: { reason: () => reason, run: () => { calls.push('bring'); } } };
    const store = { any: false, get: () => null, latest: () => null } as unknown as ConstructorParameters<typeof SaveMenuFlow>[1];
    const flow = new SaveMenuFlow(host, store, hooks);
    let row = flow.pause().items.find(i => i.id === 'bring-vehicle')!;
    expect(row).toMatchObject({ label: 'Bring my vehicle here', detail: 'Ready again in 12 s', enabled: false });
    reason = null;
    row = flow.pause().items.find(i => i.id === 'bring-vehicle')!;
    expect(row).toMatchObject({ detail: 'Parks it on the nearest street', enabled: true });
    row.run();
    expect(calls).toEqual(['bring']);
    expect(new SaveMenuFlow(host, store, { ...hooks, bringVehicle: undefined }).pause().items.some(i => i.id === 'bring-vehicle')).toBe(false);
  });
});

describe('the pizzeria loaner scooter', () => {
  /** A new game that rented its permit: 40 € left, too little for the 60 € scooter. */
  function broke() {
    const h = harness();
    h.missions.begin();
    h.choose(MARKET.point, 'rent_permit'); h.leave(MARKET.point);
    expect(h.missions.money).toBe(40);
    return h;
  }

  it('dead end 2: no vehicle and no money for one: the order goes out on the loaner at scooter par, 2 € less', () => {
    const h = broke();
    h.press(near(PIZZERIA.point), { rideAt: far });
    const quote = h.option('take_order').quote!;
    expect(h.option('take_order').enabled).toBe(true);
    expect(quote.fee).toBe(LOANER_FEE);
    expect(quote.par).toBeCloseTo(parSeconds(quote.metres, 'scooter'));
    expect(optionDetail(h.option('take_order'))).toContain('· 6 € + tip · loaner scooter −2 €');
    // The loaner was left far away: taking the order brings it back to the kerb.
    expect(h.at(near(PIZZERIA.point), 0.1, { choose: 'take_order', rideAt: far }).park).toBe(true);
    expect(h.missions.snapshot().dialogue).toContain('pizzeria_loaner');
    expect(h.missions.mayDrive(LOANER_ID)).toBe(true);
    expect(h.missions.mayDrive('parked-car')).toBe(false);
    h.at(near(PIZZERIA.point), 0.1, { vehicleId: LOANER_ID });
    // A reload mid-order keeps the fee.
    const resumed = harness(h.missions.serialize());
    resumed.press(near(resumed.missions.currentStop.point));
    expect(resumed.missions.money).toBe(40 + 6 + 8 - LOANER_FEE);
    expect(resumed.missions.snapshot().dialogue).toContain('paid_loaner_fee');
  });

  it('charges nothing when the order is run on foot, and leaves a loaner at the kerb where it is', () => {
    const h = broke();
    h.press(near(PIZZERIA.point), { rideAt: PIZZERIA_VEHICLE_SPOT });
    expect(h.at(near(PIZZERIA.point), 0.1, { choose: 'take_order', rideAt: PIZZERIA_VEHICLE_SPOT }).park).toBe(false);
    h.press(near(h.missions.currentStop.point));
    expect(h.missions.money).toBe(40 + 6 + 8);
    expect(h.missions.snapshot().dialogue).not.toContain('paid_loaner_fee');
  });

  it('takes no long or limited job, and is no delivery vehicle once a car is owned', () => {
    const h = broke();
    for (let i = 0; i < 4; i++) { h.choose(PIZZERIA.point, 'take_order', { vehicleId: LOANER_ID }); h.press(near(h.missions.currentStop.point)); }
    h.press(near(PIZZERIA.point), { vehicleId: LOANER_ID });
    expect(h.option('take_long_order').reason).toMatch(/^(Buy a delivery vehicle|No drivable)/);
    h.leave(PIZZERIA.point);
    h.missions.grantDevMoney(400);
    h.choose(DEALER.point, 'buy_car');
    h.wait(DEALER.point, 10);
    expect(h.missions.rideId).toBe('player');
    h.press(near(PIZZERIA.point), { vehicleId: LOANER_ID });
    expect(h.option('take_order').reason).toBe('Only on foot or in your own vehicle, unless you have forged plates.');
    expect(h.option('take_order').quote!.fee).toBeUndefined();
  });

  it('becomes the player\'s own scooter when bought: no fee after that', () => {
    const h = broke();
    expect(h.missions.rideId).toBe(LOANER_ID);
    h.missions.grantDevMoney(30);
    h.choose(DEALER.point, 'buy_scooter');
    h.wait(DEALER.point, 10);
    expect(h.missions.rideId).toBe(SCOOTER_ID);
    h.choose(PIZZERIA.point, 'take_order', { vehicleId: SCOOTER_ID });
    const before = h.missions.money;
    h.press(near(h.missions.currentStop.point), { vehicleId: SCOOTER_ID });
    expect(h.missions.money).toBe(before + 6 + 8);
  });
});
