import './fleet-assets.fixture';
import { describe, expect, test } from 'vitest';
import { FLEET, FLEET_BY_ID, fleetDamageBox, PLAYER_MODEL } from '../../src/actors/fleet/catalog';
import { loadAuthoredFleet, loadAuthoredFleetParts } from '../../src/actors/fleet/authored';
import { modelLayout, skinToCage } from '../../src/actors/fleet/damageRig';
import { partsOf, splitReport, windowReport, windowsOf } from '../../src/actors/fleet/damageParts';
import { applyHit, DamageState, encodeDamage, PARTS } from '../../src/vehicle/damage';

// Body damage across the fleet (vehicle/README.md "Body damage across the fleet"): the cage, weights, part split and
// windows run for every model at load (actors/fleet/damageRig.ts skinToCage); a part or window that comes out
// implausible stays on the body. These tables are the measured outcome of every shipped model (2026-11-06); a model
// re-export that changes its split shows here.
/** Parts that stay on the body (deform and wear with it, never hang loose or come off). */
const STAYS_ON_BODY: Record<string, string> = {
  'carla-audi-etron': 'rearBumper',
  'carla-audi-tt': 'bonnet rearBumper',
  'carla-carlamotors-carlacola': 'bonnet bootLid frontBumper mirrorLeft mirrorRight',
  'carla-carlamotors-european-hgv': 'bootLid mirrorLeft mirrorRight',
  'carla-carlamotors-firetruck': 'bootLid mirrorLeft mirrorRight rearBumper',
  'carla-chevrolet-impala': 'bootLid',
  'carla-dodge-charger-police-2020': 'bonnet',
  'carla-ford-ambulance': 'bonnet bootLid',
  'carla-ford-crown': 'bonnet bootLid',
  'carla-jeep-wrangler-rubicon': 'bonnet mirrorLeft mirrorRight',
  'carla-mercedes-sprinter': 'bonnet bootLid',
  'carla-mini-cooper-s-2021': 'bonnet',
  'carla-mitsubishi-fusorosa': 'bootLid mirrorLeft mirrorRight',
  'carla-tesla-cybertruck': 'bonnet bootLid mirrorLeft mirrorRight',
  'carla-volkswagen-t2-2021': 'bonnet',
  'crownvic-police': 'bonnet bootLid rearBumper',
  'crownvic-taxi': 'bonnet bootLid rearBumper',
  'euro-van': 'bootLid mirrorLeft mirrorRight',
  'fabia2': 'bootLid',
  'garbage-truck': 'bootLid mirrorLeft mirrorRight',
  'generic-sedan': 'bonnet bootLid rearBumper',
  'mazda3-bk': 'bootLid',
  'punto-1995': 'bonnet bootLid',
  's90': 'bonnet',
  'sprinter-ambulance': 'bonnet bootLid mirrorLeft mirrorRight',
  'transit-bus': 'mirrorLeft mirrorRight',
};
/** Windows that crack and shatter, where not all six (trucks without separate glazing have none). */
const WINDOWS_FOUND: Record<string, number> = {
  'carla-audi-tt': 4, 'carla-carlamotors-carlacola': 5, 'carla-carlamotors-european-hgv': 0, 'carla-carlamotors-firetruck': 0,
  'carla-ford-ambulance': 5, 'carla-ford-crown': 5, 'carla-ford-mustang': 4, 'carla-jeep-wrangler-rubicon': 3, 'carla-mercedes-coupe-2020': 4,
  'carla-mercedes-sprinter': 5, 'carla-micro-microlino': 4, 'carla-mini-cooper-s-2021': 2, 'carla-mitsubishi-fusorosa': 5,
  'carla-nissan-patrol-2021': 4, 'euro-van': 4, 'garbage-truck': 3, 'mazda3-bk': 3, 'prado-2010': 4, 'punto-1995': 4, 'rav4-xa50': 5,
  'sprinter-ambulance': 0, 'transit-bus': 3, 'v60': 4,
};
/** Windows found but implausible (a piece spanning far more than that window), kept intact. */
const WINDOWS_DROPPED: Record<string, string> = {
  'carla-ford-ambulance': 'rearRight', 'carla-ford-crown': 'frontRight', 'carla-mitsubishi-fusorosa': 'rearRight',
  'carla-nissan-patrol-2021': 'frontLeft frontRight', 'prado-2010': 'rearLeft rearRight', 'rav4-xa50': 'rearWindow',
  'transit-bus': 'rearLeft rearRight windscreen', 'v60': 'frontLeft frontRight',
};
/** Bodies that do not fit their model's box (the transit bus is 27 cm off its origin along the length). */
const MISFIT = ['transit-bus'];

describe('every fleet model gets its damage cage, weights, parts and windows (actors/fleet/damageParts.ts)', () => {
  test('the split of every shipped model: parts kept or left on the body, windows, the cage fit', async () => {
    const parts: Record<string, string> = {}, found: Record<string, number> = {}, dropped: Record<string, string> = {}, misfit: string[] = [];
    for (const m of FLEET) {
      const body = (await loadAuthoredFleetParts(m.id, 0))!.body, glass = await loadAuthoredFleet(m.id, 0, 'glass'), layout = modelLayout(m);
      const triangles = body.index!.count;
      skinToCage(body, layout); if (glass) skinToCage(glass, layout, 'glass');
      // Weights: every vertex has four bytes summing to 255 on cage nodes; no triangle is lost.
      expect(body.index!.count, m.id).toBe(triangles);
      const weight = body.getAttribute('skinWeight').array as Uint8Array, index = body.getAttribute('skinIndex').array as Uint8Array;
      for (let i = 0; i < weight.length; i += 4) {
        if (weight[i] + weight[i + 1] + weight[i + 2] + weight[i + 3] !== 255) throw Error(`${m.id} vertex ${i / 4} weights`);
        if (Math.max(index[i], index[i + 1], index[i + 2], index[i + 3]) >= layout.count) throw Error(`${m.id} vertex ${i / 4} node`);
      }
      const report = splitReport(body)!, kept = partsOf(body)!;
      const off = PARTS.filter((name, i) => !report.parts[name].kept && !kept[i]);
      expect(PARTS.filter((name, i) => report.parts[name].kept !== !!kept[i]), m.id).toEqual([]);
      if (off.length) parts[m.id] = [...off].sort().join(' ');
      if (!report.fit.ok) misfit.push(m.id);
      const windows = glass ? windowReport(glass)! : null;
      if ((windows?.windows.length ?? 0) !== 6) found[m.id] = windows?.windows.length ?? 0;
      if (windows?.dropped.length) dropped[m.id] = windows.dropped.map(d => d.split(' ')[0]).sort().join(' ');
      if (glass) expect(windowsOf(glass)!.map(w => w.window).sort(), m.id).toEqual([...windows!.windows].sort());
    }
    expect(parts).toEqual(STAYS_ON_BODY);
    expect(found).toEqual(WINDOWS_FOUND);
    expect(dropped).toEqual(WINDOWS_DROPPED);
    expect(misfit).toEqual(MISFIT);
  }, 30000);
});

describe('parked and driven fleet cars take damage; another player\'s car leaves it to that player (vehicle.ts ownDamage)', () => {
  test('a parked copy dents from this client\'s car, not from a remote player\'s; owned (driven or shared), from both', async () => {
    const R = await import('@dimforge/rapier3d');
    const { PhysicsWorld } = await import('../../src/physics/world');
    const { modelDamageBox, modelTuning } = await import('../../src/vehicle/modelTuning');
    const { decodeDamage } = await import('../../src/vehicle/damage');
    const idle = { vx: 0, vz: 0, jump: false };
    const box = modelDamageBox('passat-b8')!, layout = modelLayout(FLEET_BY_ID.get('passat-b8')!);
    /** A parked passat at x 14; something runs into it at 60 km/h along +x: the local starter car, or a remote car proxy. */
    async function hit(by: 'local' | 'remote', own: boolean) {
      const pw = new PhysicsWorld(R);
      pw.world.createCollider(R.ColliderDesc.cuboid(100, .5, 100).setTranslation(0, -.5, 0));
      pw.spawnVehicle({ x: 14, y: .8, z: 0 }, 0, { id: 'parked', parked: true, tuning: modelTuning('passat-b8'), damage: { box } });
      if (own) pw.ownVehicleDamage('parked');
      if (by === 'local') pw.spawnVehicle({ x: 4, y: .8, z: 0 }, 0, { tuning: modelTuning(PLAYER_MODEL), damage: { box: modelDamageBox(PLAYER_MODEL)! } });
      for (let i = 0; i < 30; i++) pw.fixedStep(idle, null);
      if (by === 'local') (pw as unknown as { vehicles: Map<string, { body: { setLinvel(v: object, wake: boolean): void } }> }).vehicles.get('player')!.body.setLinvel({ x: 60 / 3.6, y: 0, z: 0 }, true);
      for (let i = 0; i < 120; i++) {
        if (by === 'remote') pw.setRemotes([{ id: 'other', pos: { x: 4 + i / 60 * 60 / 3.6, y: .8, z: 0 }, yaw: -Math.PI / 2, mode: 'car' }]);
        pw.fixedStep(idle, null);
      }
      const state = pw.vehicleStates().find(v => v.id === 'parked')!, moved = state.pos.x - 14;
      const damage = new DamageState(layout);
      if (state.damage) decodeDamage(state.damage, damage);
      pw.world.free();
      return { moved, hits: damage.hits };
    }
    const local = await hit('local', false), remote = await hit('remote', false), owned = await hit('remote', true);
    expect(local.moved).toBeGreaterThan(1); expect(remote.moved).toBeGreaterThan(1); expect(owned.moved).toBeGreaterThan(1);
    expect(local.hits).toBeGreaterThan(0);
    expect(remote.hits).toBe(0);
    expect(owned.hits).toBeGreaterThan(0);
  });
  test('a shared record of any model spawns with its cage and continues from its damage', async () => {
    const { vi } = await import('vitest');
    const { SharedVehicles } = await import('../../src/net/sharedVehicles');
    const passat = FLEET_BY_ID.get('passat-b8')!, state = new DamageState(modelLayout(passat));
    applyHit(state, { dv: [0, 0, -70 / 3.6] });
    const wire = encodeDamage(state), self = 'me';
    const record = { id: 'park:car-7', source: 'car-7', creator: 'other', localId: 'car-7', owner: self, occupant: null, epoch: 2, revision: 4,
      half: { x: 2.3, y: .4, z: 1 }, pose: { t: 1, E: 10, N: -20, H: 2, yaw: 0, vE: 0, vN: 0, vH: 0, mode: 'car', vehicle: 'passat-b8/blue', carrying: false, damage: wire } };
    const client = Object.assign(new EventTarget(), { state: { connected: true, you: { id: self }, vehicles: new Map([[record.id, record]]),
      players: [], vehicleSources: new Set<string>() }, holdSeat() {}, vehicle: vi.fn() });
    const physics = { latest: { vehicles: [] }, spawnVehicle: vi.fn(), removeVehicle: vi.fn(), vehicleImpulse: vi.fn(), setVehicleDamage: vi.fn(), ownVehicleDamage: vi.fn() };
    const shared = new SharedVehicles(client as never, physics as never, { vehicles: [] } as never, { setShared() {}, resetRound() {} } as never);
    shared.update(0);
    expect(physics.spawnVehicle).toHaveBeenCalledWith(expect.anything(), expect.any(Number),
      expect.objectContaining({ damage: { box: fleetDamageBox(passat) } }));
    expect(physics.setVehicleDamage).toHaveBeenCalledWith(expect.any(String), wire);
  });
});
