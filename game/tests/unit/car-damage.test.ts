import './fleet-assets.fixture';
import { describe, expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { applyHit, cageWeights, DamageState, damageLayout, decodeDamage, encodeDamage, headOnLoss, nodeIndex, validDamageWire, DAMAGE_NODES } from '../../src/vehicle/damage';
import { fleetDamageBox, FLEET_BY_ID, PLAYER_MODEL } from '../../src/actors/fleet/catalog';
import { loadAuthoredFleetParts } from '../../src/actors/fleet/authored';
import { DAMAGE_SLOTS, DamageRig, modelLayout, rigOf, skinToCage } from '../../src/actors/fleet/damageRig';

const golf = FLEET_BY_ID.get(PLAYER_MODEL)!;
const layout = damageLayout(fleetDamageBox(golf));
// Wreckfest 2 numbers (damage-study.md) are for a 4.42 m car; the crumple scales with the car's length.
const scale = golf.length / 4.42, cm = (m: number) => m * 100;

describe('crumple model against the Wreckfest 2 table', () => {
  test('single head-on hits: none at walking pace, ~20 cm at 50-60 km/h, 33 at 70, 42 at 80', () => {
    expect(cm(headOnLoss(layout, 30))).toBeLessThan(3); // the soft bumper layer only
    expect(cm(headOnLoss(layout, 15))).toBeLessThan(.01);
    for (const [kmh, wreckfest, tolerance] of [[52, 21, 5], [61.5, 24, 5], [71.5, 33.3, 3], [81, 42, 3]] as const)
      expect(Math.abs(cm(headOnLoss(layout, kmh)) - wreckfest * scale), `${kmh} km/h`).toBeLessThan(tolerance);
  });
  test('repeat hits saturate at the hard stop: six at 61.5 km/h go 28 → 46 cm, eight at 42.5 about equal one at 70', () => {
    const six = [1, 2, 3, 4, 5, 6].map(n => cm(headOnLoss(layout, 61.5, n)));
    [28, 32, 38, 42, 44, 46].forEach((wreckfest, i) => expect(Math.abs(six[i] - wreckfest * scale), `hit ${i + 1}`).toBeLessThan(5));
    for (let i = 1; i < six.length; i++) expect(six[i] - six[i - 1]).toBeLessThan(i === 1 ? 10 : 6);
    // The hard stop holds at the least crushed point (bumper layer and the end's unevenness on top).
    expect(cm(headOnLoss(layout, 61.5, 20))).toBeLessThanOrEqual(cm(.105 * golf.length * 1.1 + .03));
    expect(Math.abs(cm(headOnLoss(layout, 42.5, 8)) - cm(headOnLoss(layout, 70)))).toBeLessThan(5);
  });
  test('the cabin stays nearly rigid and the cage never folds through itself', () => {
    const state = new DamageState(layout);
    for (let i = 0; i < 12; i++) applyHit(state, { dv: [0, 0, -85 / 3.6] });
    for (let i = 0; i < 12; i++) applyHit(state, { dv: [0, 0, 85 / 3.6] });
    const cabin = [3, 4, 5];
    for (const s of cabin) for (const h of [0, 1]) for (const l of [0, 1, 2]) {
      const i = nodeIndex(s, h, l) * 3;
      expect(Math.hypot(state.offsets[i], state.offsets[i + 1], state.offsets[i + 2]), `station ${s}`).toBeLessThan(.016);
    }
    // Stations keep their order along the length after the worst crumple at both ends.
    for (const h of [0, 1]) for (const l of [0, 1, 2]) for (let s = 1; s < layout.stations.length; s++) {
      const z = (k: number) => layout.stations[k] + state.offsets[nodeIndex(k, h, l) * 3 + 2];
      expect(z(s) - z(s - 1), `stations ${s - 1}-${s}`).toBeGreaterThan(.02);
    }
  });
  test('a pole dents deeper under it than beside it; a side hit dents the doors, not the far side', () => {
    const pole = new DamageState(layout);
    applyHit(pole, { dv: [0, 0, -60 / 3.6], at: [0, .6, 2], halfWidth: .15 });
    const front = (l: number) => -pole.offsets[nodeIndex(8, 0, l) * 3 + 2];
    expect(front(1)).toBeGreaterThan(front(0) * 1.5);
    const side = new DamageState(layout);
    applyHit(side, { dv: [-50 / 3.6, 0, 0], at: [1, .6, 0] }); // pushed towards -X: the +X side was hit
    const x = (l: number) => side.offsets[nodeIndex(4, 1, l) * 3];
    expect(x(2)).toBeLessThan(-.08);
    expect(Math.abs(x(0))).toBeLessThan(.01);
  });
  test('light hits leave no shape and count no hit; a landing from below does nothing', () => {
    const state = new DamageState(layout);
    expect(applyHit(state, { dv: [0, 0, -10 / 3.6] })).toBe(0);
    expect(applyHit(state, { dv: [0, 60 / 3.6, 0] })).toBe(0);
    expect(state.hits).toBe(0);
    expect(state.offsets.every(v => v === 0)).toBe(true);
  });
});

describe('compact state', () => {
  test('round-trips offsets (5 mm), bins, hits and energy; rejects malformed input', () => {
    const state = new DamageState(layout);
    applyHit(state, { dv: [-20 / 3.6, 0, -70 / 3.6] });
    applyHit(state, { dv: [0, 0, 55 / 3.6], halfWidth: .2, at: [-.5, .5, -2] });
    const wire = encodeDamage(state), copy = new DamageState(layout);
    expect(wire.length).toBe(348);
    expect(decodeDamage(wire, copy)).toBe(true);
    expect(copy.hits).toBe(2);
    expect(Math.abs(copy.energy - state.energy)).toBeLessThan(64);
    for (let i = 0; i < DAMAGE_NODES * 3; i++) expect(Math.abs(copy.offsets[i] - state.offsets[i])).toBeLessThanOrEqual(.0025 + 1e-6);
    for (let i = 0; i < state.bins.length; i++) expect(Math.abs(copy.bins[i] - state.bins[i])).toBeLessThanOrEqual(4);
    expect(encodeDamage(copy)).toBe(wire);
    expect(validDamageWire(wire)).toBe(true);
    for (const bad of [null, 3, '', 'not base64!', wire.slice(4), `${wire}AAAA`]) expect(validDamageWire(bad), String(bad)).toBe(false);
  });
});

describe('cage skinning of the real starter car', () => {
  test('weights: at most four nodes summing to one, exact for an affine offset field', () => {
    const index = [0, 0, 0, 0], weight = [0, 0, 0, 0];
    for (const [x, y, z] of [[0, .5, 0], [.3, 1.1, 1.9], [-.9, .1, -2.1], [.757, .997, 1.53]]) {
      cageWeights(layout, x, y, z, index, weight);
      expect(weight.reduce((a, b) => a + b)).toBeCloseTo(1, 6);
      expect(weight.every(w => w >= -1e-9)).toBe(true);
      // An affine field u(p) = A p is reproduced inside the cage.
      const u = (i: number) => [layout.nodes[i * 3] * .1, layout.nodes[i * 3 + 2] * -.05, layout.nodes[i * 3 + 1] * .2];
      const blend = [0, 1, 2].map(k => index.reduce((s, n, j) => s + weight[j] * u(n)[k], 0));
      const inside = (v: number, a: ArrayLike<number>) => Math.max(a[0], Math.min(a[a.length - 1], v));
      const p = [inside(x, layout.laterals), inside(y, layout.levels), inside(z, layout.stations)];
      expect(blend[0]).toBeCloseTo(p[0] * .1, 5); expect(blend[1]).toBeCloseTo(p[2] * -.05, 5); expect(blend[2]).toBeCloseTo(p[1] * .2, 5);
    }
  });
  test('an 80 km/h wall hit shortens the drawn Golf by the model depth; the cabin and the rear stay put', async () => {
    const parts = (await loadAuthoredFleetParts(PLAYER_MODEL, 0))!;
    const cage = modelLayout(golf);
    skinToCage(parts.body, cage);
    const skinIndex = parts.body.getAttribute('skinIndex'), skinWeight = parts.body.getAttribute('skinWeight');
    expect(skinIndex.count).toBe(parts.body.getAttribute('position').count);
    for (let i = 0; i < skinWeight.count; i += 97) {
      expect([0, 1, 2, 3].reduce((s, k) => s + skinWeight.getComponent(i, k), 0)).toBeCloseTo(1, 5);
      for (let k = 0; k < 4; k++) expect(skinIndex.getComponent(i, k)).toBeLessThan(DAMAGE_NODES);
    }
    const rig = new DamageRig(cage), body = parts.body;
    body.computeBoundingBox();
    const fresh = rig.bounds(body);
    expect(fresh.max.z).toBeCloseTo(body.boundingBox!.max.z, 4);
    const state = new DamageState(cage);
    applyHit(state, { dv: [0, 0, -80 / 3.6] });
    rig.show(encodeDamage(state));
    expect(rig.slot).toBeGreaterThan(0);
    const hit = rig.bounds(body);
    const loss = fresh.max.z - hit.max.z;
    expect(Math.abs(loss - headOnLoss(cage, 80))).toBeLessThan(.03);
    expect(Math.abs(hit.min.z - fresh.min.z)).toBeLessThan(.005);
    // Bulge: the crushed front grows a little across and up (Wreckfest 2: 1-3 cm).
    expect(hit.max.x - hit.min.x - (fresh.max.x - fresh.min.x)).toBeGreaterThanOrEqual(-.005);
    // Cabin vertices (between the A and C pillars) move under 1.5 cm.
    const position = parts.body.getAttribute('position'), v = new THREE.Vector3();
    let cabin = 0;
    for (let i = 0; i < position.count; i++) {
      v.fromBufferAttribute(position, i);
      if (Math.abs(v.z) > .6) continue;
      const moved = rig.deform(body, i, new THREE.Vector3()).distanceTo(v);
      cabin = Math.max(cabin, moved);
    }
    expect(cabin).toBeLessThan(.015);
    rig.show(undefined);
    expect(rig.slot).toBe(0);
    expect(rig.bounds(body).max.z).toBeCloseTo(fresh.max.z, 5);
  }, 30_000);
  test('damaged cars share the pool: each holds a slot, the oldest gives its slot up when it overflows', () => {
    const state = new DamageState(layout);
    applyHit(state, { dv: [0, 0, -60 / 3.6] });
    const wire = encodeDamage(state), rigs = Array.from({ length: DAMAGE_SLOTS }, () => new DamageRig(layout));
    const mesh = new THREE.Object3D();
    rigs[0].bind(mesh);
    expect(rigOf(mesh)).toBe(rigs[0]);
    for (const rig of rigs) rig.show(wire);
    // 31 damaged slots: the first car shown is the oldest and draws fresh until it is shown again.
    expect(new Set(rigs.slice(1).map(r => r.slot)).size).toBe(DAMAGE_SLOTS - 1);
    expect(rigs[0].slot).toBe(0);
    rigs[0].show(wire);
    expect(rigs[0].slot).toBeGreaterThan(0);
    expect(rigs[1].slot).toBe(0);
    for (const rig of rigs) rig.release();
  });
});

describe('the physics worker owns the damage state', () => {
  test('a dent reaches the published state, also of a sleeping car, and decodes to the same shape', async () => {
    const R = await import('@dimforge/rapier3d');
    const { PhysicsWorld } = await import('../../src/physics/world');
    const { modelDamageBox, modelTuning } = await import('../../src/vehicle/modelTuning');
    const pw = new PhysicsWorld(R);
    pw.world.createCollider(R.ColliderDesc.cuboid(50, .5, 50).setTranslation(0, -.5, 0));
    pw.spawnVehicle({ x: 0, y: .8, z: 0 }, 0, { tuning: modelTuning(PLAYER_MODEL), damage: { box: modelDamageBox(PLAYER_MODEL)! } });
    const idle = { vx: 0, vz: 0, jump: false };
    for (let i = 0; i < 240; i++) pw.fixedStep(idle, null);
    expect(pw.vehicleStates()[0].damage).toBeUndefined();
    const sleeping = pw.vehicle!.body.isSleeping();
    pw.damageVehicle('player', { dv: [0, 0, -70 / 3.6] });
    const wire = pw.vehicleStates()[0].damage!;
    expect(typeof wire).toBe('string');
    expect(pw.vehicle!.body.isSleeping()).toBe(sleeping);
    const drawn = new DamageState(layout);
    expect(decodeDamage(wire, drawn)).toBe(true);
    const front = Math.max(...[0, 1].flatMap(h => [0, 1, 2].map(l => drawn.offsets[nodeIndex(8, h, l) * 3 + 2])));
    expect(Math.abs(cm(-front) - cm(headOnLoss(layout, 70)))).toBeLessThan(.5);
    // A respawn of the same car keeps its damage; a car without a cage ignores dents.
    pw.spawnVehicle({ x: 3, y: .8, z: 0 }, 0, { tuning: modelTuning(PLAYER_MODEL) });
    expect(pw.vehicleStates()[0].damage).toBe(wire);
    pw.spawnVehicle({ x: 10, y: .8, z: 0 }, 0, { id: 'plain' });
    pw.damageVehicle('plain', { dv: [0, 0, -70 / 3.6] });
    expect(pw.vehicleStates().find(v => v.id === 'plain')!.damage).toBeUndefined();
    pw.setVehicleDamage('player', null);
    expect(pw.vehicleStates()[0].damage).toBeUndefined();
    pw.world.free();
  });
});

describe('crashes in the physics world become hits', () => {
  /** Drives the starter car (coasting, no input brake) into a 1 m thick wall `hits` times at `kmh`; per hit the
   *  impact speed and the front loss of the published state. */
  async function crash(kmh: number, { yawDeg = 0, hits = 1, trimesh = false } = {}) {
    const R = await import('@dimforge/rapier3d');
    const { PhysicsWorld } = await import('../../src/physics/world');
    const { modelDamageBox, modelTuning } = await import('../../src/vehicle/modelTuning');
    const pw = new PhysicsWorld(R), idle = { vx: 0, vz: 0, jump: false }, coast = { throttle: 0, steer: 0, brake: 0, handbrake: false };
    pw.world.createCollider(R.ColliderDesc.cuboid(100, .5, 100).setTranslation(0, -.5, 0));
    // Building walls in the tiles are trimeshes, which report no contact impulses: the chassis momentum still sees them.
    if (trimesh) pw.world.createCollider(R.ColliderDesc.trimesh(new Float32Array([20, 0, -10, 20, 0, 10, 20, 5, 10, 20, 5, -10]), new Uint32Array([0, 1, 2, 0, 2, 3])));
    else pw.world.createCollider(R.ColliderDesc.cuboid(.5, 3, 10).setTranslation(20.5, 3, 0));
    pw.spawnVehicle({ x: 0, y: .8, z: 0 }, 0, { tuning: modelTuning(PLAYER_MODEL), damage: { box: modelDamageBox(PLAYER_MODEL)! } });
    const out: { impact: number; loss: number; state: DamageState }[] = [];
    for (let h = 0; h < hits; h++) {
      pw.resetVehicle({ x: 14, y: .8, z: 0 }, yawDeg * Math.PI / 180);
      for (let i = 0; i < 20; i++) pw.fixedStep(idle, coast);
      pw.vehicle!.body.setLinvel({ x: kmh / 3.6, y: 0, z: 0 }, true);
      let impact = 0;
      for (let i = 0; i < 150; i++) {
        const before = pw.vehicle!.body.linvel().x;
        pw.fixedStep(idle, coast);
        if (!impact && pw.vehicle!.body.linvel().x < before - 1) impact = before * 3.6;
      }
      const state = new DamageState(layout), wire = pw.vehicleStates()[0].damage;
      if (wire) decodeDamage(wire, state);
      const front = Math.max(...[0, 1].flatMap(level => [0, 1, 2].map(l => state.offsets[nodeIndex(8, level, l) * 3 + 2])));
      out.push({ impact, loss: -front, state });
    }
    pw.world.free();
    return out;
  }
  test('head-on wall hits at 30, 50 and 80 km/h crumple as the measured curve, against boxes and trimeshes', async () => {
    for (const kmh of [30, 50, 80]) {
      const [hit] = await crash(kmh);
      expect(hit.impact).toBeGreaterThan(kmh - 3);
      expect(hit.state.hits, `${kmh} km/h`).toBe(1);
      expect(Math.abs(cm(hit.loss) - cm(headOnLoss(layout, hit.impact))), `${kmh} km/h`).toBeLessThan(1.5);
    }
    const [mesh] = await crash(50, { trimesh: true });
    expect(cm(mesh.loss)).toBeGreaterThan(10);
  });
  test('repeat hits at 61.5 km/h saturate; an off-square hit crushes one corner and hides behind the other', async () => {
    const hits = await crash(61.5, { hits: 4 });
    hits.forEach((hit, i) => expect(Math.abs(cm(hit.loss) - cm(headOnLoss(layout, hit.impact, i + 1))), `hit ${i + 1}`).toBeLessThan(2));
    expect(hits[3].state.hits).toBe(4);
    const [angled] = await crash(50, { yawDeg: 10 });
    const bins = angled.state.bins;
    expect(Math.max(bins[0], bins[2])).toBeGreaterThan(3 * Math.min(bins[0], bins[2]));
    expect(cm(angled.loss)).toBeLessThan(8);
  });
});

describe('damage survives saves and travels with the shared vehicle record', () => {
  const damaged = () => {
    const state = new DamageState(layout);
    applyHit(state, { dv: [0, 0, -70 / 3.6] });
    return encodeDamage(state);
  };
  test('a save keeps the start car damage; loading puts it back, a new game starts with a fresh body', async () => {
    const { vi } = await import('vitest');
    const { SaveGameController } = await import('../../src/gameplay/saveGame');
    const { SaveStore, SAVES_KEY } = await import('../../src/core/save');
    const { draftManifest } = await import('../../src/world/manifest');
    const { parseRoadGraph } = await import('../../src/gameplay/roadGraph');
    vi.stubGlobal('window', new EventTarget());
    vi.stubGlobal('document', Object.assign(new EventTarget(), { visibilityState: 'visible' }));
    const wire = damaged(), ground = { toi: 2, point: { x: 10, y: 2, z: 20 }, normal: { x: 0, y: 1, z: 0 }, material: 1, tile: 'c_0_0' };
    const car = { id: 'player', kind: 'car' as const, pos: { x: 15, y: 2.75, z: 20 }, rot: { x: 0, y: 0, z: 0, w: 1 }, linvel: { x: 0, y: 0, z: 0 },
      speed: 0, forwardSpeed: 0, wheels: [], curbStrike: null, damage: wire };
    const physics = { latest: { vehicles: [car], character: { pos: { x: 10, y: 2, z: 20 } } }, loaded: new Map([['c_0_0', {}]]),
      resetVehicle: vi.fn(), setVehicleDamage: vi.fn(), spawnVehicle: vi.fn(), setCharacterEnabled: vi.fn(), step: vi.fn(), driveVehicle: vi.fn(),
      raycast: vi.fn(async (_p: unknown, dir: { y: number }, _d: number, filter?: string) => filter === 'water' || dir.y === 1 ? null : ground),
      cameraCast: vi.fn(async (_p: unknown, _d: unknown, distance: number) => distance) };
    const store = { value: '', getItem(k: string) { return k === SAVES_KEY ? this.value || null : null; }, setItem(_k: string, v: string) { this.value = v; }, removeItem() {} };
    const o = { physics, streamer: { update() {} }, manifest: draftManifest('http://localhost/'),
      router: { graph: parseRoadGraph({ format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox: [0, 0, 50, 50], nodes: [0, 0, 50, 0], edges: [[0, 1, 3, 0]] }) },
      missions: { vehicle: 'none', lastAddress: null, serialize: () => '{}', restore: vi.fn(), begin: vi.fn(), deliveries: 0, money: 0, devMoney: 0 },
      recovery: { restart: vi.fn() }, saves: new SaveStore(store, () => { throw Error(); }),
      startScreen: { active: false, close() {}, show() {} }, pauseMenu: { close() {}, show() {} },
      clock: () => new Date(), spawn: { x: 10, y: 2, z: 20, yaw: 0 }, car: { x: 15, y: 2, z: 20, yaw: 0 },
      view: () => ({ mode: 'foot', drivenId: null, feet: { x: 10, y: 2, z: 20 }, cameraYaw: 1 }),
      leaveCar() {}, placed: vi.fn(), enterVehicle: vi.fn(), setPaused: vi.fn(), showIntro: vi.fn(), fail: vi.fn() };
    const controller = new SaveGameController(o as unknown as ConstructorParameters<typeof SaveGameController>[0]);
    window.dispatchEvent(new Event('pagehide'));
    const game = new SaveStore(store, () => { throw Error(); }).get('auto')!;
    expect(game.carDamage).toBe(wire);
    controller.dispose();
    const reload = new SaveGameController(o as unknown as ConstructorParameters<typeof SaveGameController>[0]);
    await reload.prepare(game);
    reload.hooks.play(game);
    await vi.waitFor(() => expect(o.setPaused).toHaveBeenLastCalledWith(false));
    expect(physics.setVehicleDamage).toHaveBeenLastCalledWith('player', wire);
    await reload.prepare(null);
    reload.hooks.play(null);
    await vi.waitFor(() => expect(physics.setVehicleDamage).toHaveBeenLastCalledWith('player', null));
    expect(o.fail).not.toHaveBeenCalled();
    reload.dispose();
    vi.unstubAllGlobals();
  });
  test('the owner sends its state in the vehicle pose; the server keeps it; a replica draws the same shape', async () => {
    const { vehiclePose, vehicleFingerprint } = await import('../../src/net/sharedVehicles');
    const { cleanPose, validDamage, validPose } = await import('../../../server/pose.js');
    const wire = damaged();
    const car = { id: 'player', kind: 'car' as const, pos: { x: 1, y: 2, z: 3 }, rot: { x: 0, y: 0, z: 0, w: 1 }, linvel: { x: 0, y: 0, z: 0 },
      speed: 0, forwardSpeed: 0, wheels: [], curbStrike: null };
    const fresh = vehiclePose(car, 'player', 5), hit = vehiclePose({ ...car, damage: wire }, 'player', 5);
    expect(fresh.damage).toBeUndefined();
    expect(hit.damage).toBe(wire);
    // A new shape is a change to send even when the car stands still.
    expect(vehicleFingerprint(hit)).not.toBe(vehicleFingerprint(fresh));
    expect(validDamage(wire)).toBe(true);
    expect(validPose(hit)).toBe(true);
    const relayed = cleanPose(hit);
    expect(relayed.damage).toBe(wire);
    for (const bad of [wire.slice(1), `${wire}A`, 12, wire.replace(/.$/, '!')]) {
      expect(validPose({ ...hit, damage: bad }), String(bad).slice(0, 8)).toBe(false);
      expect(cleanPose({ ...hit, damage: bad }).damage).toBeUndefined();
    }
    const owner = new DamageRig(layout), replica = new DamageRig(layout);
    owner.show(wire); replica.show(relayed.damage);
    const v = new THREE.Vector3(), w = new THREE.Vector3(), geometry = new THREE.BoxGeometry(1.9, 1.3, 4.2, 4, 4, 8).translate(0, .7, 0);
    skinToCage(geometry, layout);
    for (let i = 0; i < geometry.getAttribute('position').count; i += 7) expect(owner.deform(geometry, i, v).distanceTo(replica.deform(geometry, i, w))).toBe(0);
    owner.release(); replica.release();
  });
  test('an owner taking over a starter car record continues from its damage', async () => {
    const { vi } = await import('vitest');
    const { SharedVehicles } = await import('../../src/net/sharedVehicles');
    const wire = damaged(), self = 'me';
    const record = { id: 'other:player', source: null, creator: 'other', localId: 'player', owner: self, occupant: null, epoch: 2, revision: 4,
      half: { x: 2, y: .4, z: .9 }, pose: { t: 1, E: 10, N: -20, H: 2, yaw: 0, vE: 0, vN: 0, vH: 0, mode: 'car', vehicle: 'player', carrying: false, damage: wire } };
    const client = Object.assign(new EventTarget(), { state: { connected: true, you: { id: self }, vehicles: new Map([[record.id, record]]),
      players: [], vehicleSources: new Set<string>() }, holdSeat() {}, vehicle: vi.fn() });
    const physics = { latest: { vehicles: [] }, spawnVehicle: vi.fn(), removeVehicle: vi.fn(), vehicleImpulse: vi.fn(), setVehicleDamage: vi.fn() };
    const shared = new SharedVehicles(client as never, physics as never, { vehicles: [] } as never, { setShared() {}, resetRound() {} } as never);
    shared.update(0);
    expect(physics.spawnVehicle).toHaveBeenCalledWith(expect.anything(), expect.any(Number),
      expect.objectContaining({ id: record.id, damage: { box: fleetDamageBox(golf) } }));
    expect(physics.setVehicleDamage).toHaveBeenCalledWith(record.id, wire);
  });
});
