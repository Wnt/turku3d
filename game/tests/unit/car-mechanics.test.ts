import './fleet-assets.fixture';
import { describe, expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { applyHit, DamageState, damageLayout, decodeDamage, encodeDamage, WEAR, type DamageHit } from '../../src/vehicle/damage';
import { mechanics, mechanicsOf } from '../../src/vehicle/mechanics';
import { fleetDamageBox, FLEET_BY_ID, PLAYER_MODEL } from '../../src/actors/fleet/catalog';
import { roughEngine } from '../../src/audio/mix';
import { TyreEffects } from '../../src/vehicle/tyreEffects';

// Mechanical damage (vehicle/README.md "Mechanical damage") against the Wreckfest 2 study
// (~/turku3d/research/wreckfest2/damage-study.md, Normal damage, head-on and repeat-hit tables).
const layout = damageLayout(fleetDamageBox(FLEET_BY_ID.get(PLAYER_MODEL)!));
const headOn = (kmh: number): DamageHit => ({ dv: [0, 0, -kmh / 3.6] });
function hits(list: readonly DamageHit[]) {
  const state = new DamageState(layout);
  return list.map(hit => { applyHit(state, hit); return { ...mechanics(state), state }; });
}
const repeat = (kmh: number, n: number) => hits(Array.from({ length: n }, () => headOn(kmh)));

describe('mechanical damage levels against the Wreckfest 2 table', () => {
  test('single head-on hits: nothing up to 72 km/h, the radiator and both front zones from about 75', () => {
    for (const kmh of [22, 33, 43, 52, 62, 72]) {
      const [m] = repeat(kmh, 1);
      expect(m.worst, `${kmh} km/h`).toBe(0);
      expect(m.power).toBe(1);
    }
    for (const kmh of [75, 81]) {
      const [m] = repeat(kmh, 1);
      expect([m.cooling, m.engine, m.steering, ...m.suspension], `${kmh} km/h`).toEqual([1, 0, 0, 0, 0, 0, 0]);
      expect(m.zones).toEqual([1, 1, 0, 0]);
      expect(m.steam).toBeGreaterThan(0); expect(m.smoke).toBe(0);
    }
  });
  test('repeat hits: the radiator first, the hit-side zone steps to terminal, then the engine; the rest holds', () => {
    // 61.5 km/h x 6: radiator 1 at hit 2 and 2 at hit 4, front zone 1 → 4 over hits 2-5, engine 1 at hit 6.
    const at61 = repeat(61.5, 6);
    expect(at61.map(m => m.cooling)).toEqual([0, 1, 1, 2, 2, 2]);
    expect(at61.map(m => m.zones[0])).toEqual([0, 1, 2, 3, 4, 4]);
    expect(at61.map(m => m.engine)).toEqual([0, 0, 0, 0, 0, 1]);
    expect(at61.every(m => m.steering === 0 && m.suspension.every(s => s === 0))).toBe(true);
    // 42.5 km/h x 8: nothing for six hits, the radiator at the seventh.
    expect(repeat(42.5, 8).map(m => m.cooling)).toEqual([0, 0, 0, 0, 0, 0, 1, 1]);
    // Wear keeps counting after the shape has saturated.
    expect(at61[5].state.wear[WEAR.engine]).toBeCloseTo(6 * (61.5 ** 2 - 35 ** 2), -2);
  });
  test('location: a rear hit spares the engine bay; a side hit at a front wheel wears that corner and the steering', () => {
    const [rear] = hits([{ dv: [0, 0, 80 / 3.6] }]);
    expect([rear.cooling, rear.engine, rear.state.wear[WEAR.steering]]).toEqual([0, 0, 0]);
    expect(rear.zones).toEqual([0, 0, 1, 1]);
    // The car's left (+X) front wheel struck from the side: pushed towards -X.
    const [side] = hits([{ dv: [-60 / 3.6, 0, 0], at: [1, .5, 1.3], halfWidth: .3 }]);
    const w = side.state.wear, c = WEAR.corners;
    expect(w[c]).toBeGreaterThan(2 * w[c + 2]);
    expect(w[c + 1] + w[c + 3]).toBe(0);
    expect(w[WEAR.steering]).toBeGreaterThan(0); expect(w[WEAR.cooling]).toBe(0);
    expect(side.state.steerSide).toBe(1);
    // A corner hit on the right pulls right.
    const [corner] = hits([{ dv: [0, 0, -70 / 3.6], at: [-.8, .5, 2.1], halfWidth: .2 }]);
    expect(corner.state.steerSide).toBe(-1);
    expect(corner.state.wear[c + 1]).toBeGreaterThan(corner.state.wear[c]);
  });
});

describe('the mechanics travel in the damage wire', () => {
  test('wear and the steering side round-trip; a version-1 body state still reads, unworn', () => {
    const [m] = hits([{ dv: [-20 / 3.6, 0, -80 / 3.6], at: [.6, .5, 2] }]);
    const wire = encodeDamage(m.state), back = new DamageState(layout);
    expect(wire).toHaveLength(300);
    expect(decodeDamage(wire, back)).toBe(true);
    for (let i = 0; i < back.wear.length; i++) expect(Math.abs(back.wear[i] - m.state.wear[i])).toBeLessThanOrEqual(4);
    expect(back.steerSide).toBe(m.state.steerSide);
    // Levels exactly; the alignment within the wear quantization (8 (km/h)²).
    const levels = ({ cooling, engine, steering, suspension, zones, power }: ReturnType<typeof mechanics>) => ({ cooling, engine, steering, suspension, zones, power });
    expect(levels(mechanics(back))).toEqual(levels(mechanics(m.state)));
    expect(mechanics(back).steer).toBeCloseTo(mechanics(m.state).steer, 5);
    mechanics(back).toe.forEach((toe, i) => expect(toe).toBeCloseTo(mechanics(m.state).toe[i], 5));
    expect(mechanicsOf(wire)).toEqual(mechanics(back));
    // Version 1: 208 bytes, the body only (saves from before the mechanics).
    const v1 = Uint8Array.from(atob(wire), c => c.charCodeAt(0)).slice(0, 208); v1[0] = 1;
    const old = new DamageState(layout);
    expect(decodeDamage(btoa(String.fromCharCode(...v1)), old)).toBe(true);
    expect(Array.from(old.offsets)).toEqual(Array.from(back.offsets));
    expect(old.wear.every(x => x === 0)).toBe(true);
    expect(mechanics(old).worst).toBe(0);
    // A wrong version for the length, or a steering side out of range, is malformed.
    const bad = Uint8Array.from(atob(wire), c => c.charCodeAt(0)); bad[0] = 1;
    expect(decodeDamage(btoa(String.fromCharCode(...bad)), old)).toBe(false);
    bad[0] = 2; bad[bad.length - 2] = 5;
    expect(decodeDamage(btoa(String.fromCharCode(...bad)), old)).toBe(false);
  });
  test('the server relays the mechanics in the vehicle pose; a replica reads the same levels', async () => {
    const { vehiclePose } = await import('../../src/net/sharedVehicles');
    const { cleanPose, validPose } = await import('../../../server/pose.js');
    const wire = encodeDamage(repeat(61.5, 6)[5].state);
    const car = { id: 'player', kind: 'car' as const, pos: { x: 1, y: 2, z: 3 }, rot: { x: 0, y: 0, z: 0, w: 1 },
      linvel: { x: 0, y: 0, z: 0 }, speed: 0, forwardSpeed: 0, wheels: [], curbStrike: null, damage: wire };
    const pose = vehiclePose(car, 'player', 5);
    expect(validPose(pose)).toBe(true);
    const relayed = cleanPose(pose).damage;
    expect(relayed).toBe(wire);
    expect(mechanicsOf(relayed)).toMatchObject({ cooling: 2, engine: 1, zones: [4, 4, 0, 0] });
    expect(validPose({ ...pose, damage: 'A'.repeat(516) })).toBe(false);
  });
});

describe('effects', () => {
  /** A flat 3 km road along +X with the starter car at rest at its west end, damaged with `wire`. */
  async function road(wire: string | null) {
    const R = await import('@dimforge/rapier3d');
    const { PhysicsWorld } = await import('../../src/physics/world');
    const { modelDamageBox, modelTuning } = await import('../../src/vehicle/modelTuning');
    const pw = new PhysicsWorld(R), idle = { vx: 0, vz: 0, jump: false };
    pw.world.createCollider(R.ColliderDesc.cuboid(1600, .5, 200).setTranslation(0, -.5, 0));
    pw.spawnVehicle({ x: -1500, y: .8, z: 0 }, 0, { tuning: modelTuning(PLAYER_MODEL), damage: { box: modelDamageBox(PLAYER_MODEL)! } });
    pw.setVehicleDamage('player', wire);
    const step = (input: { throttle?: number; brake?: number; steer?: number } = {}) =>
      pw.fixedStep(idle, { throttle: 0, steer: 0, brake: 0, handbrake: false, ...input });
    for (let i = 0; i < 60; i++) step();
    const body = pw.vehicle!.body;
    /** Heading (rad, + is a turn to the left) and yaw rate. */
    const heading = () => { const q = body.rotation(); return Math.atan2(2 * (q.w * q.y + q.x * q.z), 1 - 2 * (q.y * q.y + q.z * q.z)); };
    return { pw, step, body, heading, done: () => pw.world.free() };
  }
  /** Full throttle from rest for 30 s with a driver holding the heading (a damaged car pulls): seconds to 50 and
   *  80 km/h and the speed at the end. */
  async function drive(wire: string | null) {
    const { step, body, heading, done } = await road(wire);
    let t50 = NaN, t80 = NaN, top = 0;
    for (let i = 1; i <= 30 * 60; i++) {
      step({ throttle: 1, steer: Math.max(-1, Math.min(1, 3 * heading() + .5 * body.angvel().y)) });
      top = Math.hypot(body.linvel().x, body.linvel().z) * 3.6;
      if (Number.isNaN(t50) && top >= 50) t50 = i / 60;
      if (Number.isNaN(t80) && top >= 80) t80 = i / 60;
    }
    done();
    return { t50, t80, top };
  }
  /** Wreckfest 2's pull probe: from rest, 2.5 s of straight full reverse, hands off; the heading change (degrees). */
  async function reversePull(wire: string | null) {
    const { step, heading, done } = await road(wire);
    const start = heading();
    for (let i = 0; i < 150; i++) step({ brake: 1 });
    const pull = (heading() - start) * 180 / Math.PI;
    done();
    return pull;
  }
  /** Hands off at a steady ~80 km/h: yaw rate (degrees/s, + left) over 3 s once settled, and whether every wheel kept
   *  its contact throughout. */
  async function cruise(wire: string | null) {
    const { pw, step, body, heading, done } = await road(wire);
    for (let i = 0; i < 4 * 60; i++) step({ throttle: 1, steer: Math.max(-1, Math.min(1, 3 * heading() + .5 * body.angvel().y)) });
    let contact = true, h0 = 0;
    for (let i = 0; i < 4 * 60; i++) {
      step({ throttle: Math.hypot(body.linvel().x, body.linvel().z) < 22 ? 1 : .3 });
      if (i === 60) h0 = heading();
      contact &&= pw.vehicleStates()[0].wheels.every(w => w.contact);
    }
    const rate = (heading() - h0) / 3 * 180 / Math.PI, kmh = Math.hypot(body.linvel().x, body.linvel().z) * 3.6;
    done();
    return { rate, contact, kmh };
  }
  const wireOf = (list: readonly DamageHit[]) => encodeDamage(hits(list).at(-1)!.state);
  test('power loss: a leaking radiator costs a little, a damaged engine more; the car stays drivable', async () => {
    const fresh = await drive(null), radiator = await drive(wireOf([headOn(80)]));
    const wrecked = await drive(wireOf(Array.from({ length: 6 }, () => headOn(61.5))));
    // The starter Golf: 0-50 km/h in about 2.2 s, 0-80 in 4.4, about 100 km/h after 30 s.
    expect(fresh.t50).toBeGreaterThan(1.8); expect(fresh.t50).toBeLessThan(2.7);
    expect(fresh.top).toBeGreaterThan(95);
    // Radiator 1 (5 % power): about 5 % slower to 50 and 80 km/h, 1-3 % lower speed.
    expect(radiator.t50 / fresh.t50).toBeGreaterThan(1.02); expect(radiator.t50 / fresh.t50).toBeLessThan(1.1);
    expect(radiator.top / fresh.top).toBeGreaterThan(.97); expect(radiator.top / fresh.top).toBeLessThan(.995);
    // Radiator 2 and engine 1 (77 % power): about a third slower, 6-11 % lower speed, still past 80 km/h.
    expect(wrecked.t80 / fresh.t80).toBeGreaterThan(1.25); expect(wrecked.t80 / fresh.t80).toBeLessThan(1.6);
    expect(wrecked.top / fresh.top).toBeGreaterThan(.89); expect(wrecked.top / fresh.top).toBeLessThan(.94);
  });
  test('steering pull after frontal hits within the Wreckfest 2 probe: none fresh or at 50 km/h, mild from 75', async () => {
    // Wreckfest 2: -0.14 ± 0.02° fresh, -0.5 to -1.0° after an 80 km/h hit, i.e. 0.36-0.86° more.
    const fresh = await reversePull(null);
    expect(Math.abs(fresh)).toBeLessThan(.05);
    expect(Math.abs(await reversePull(wireOf([headOn(50)])) - fresh)).toBeLessThan(.05);
    for (const kmh of [75, 80]) {
      const pull = Math.abs(await reversePull(wireOf([headOn(kmh)])) - fresh);
      expect(pull, `${kmh} km/h`).toBeGreaterThan(.36); expect(pull, `${kmh} km/h`).toBeLessThan(.86);
    }
    // Six hits at 61.5 km/h pull more, still a correctable drift at speed with every wheel down.
    const six = wireOf(Array.from({ length: 6 }, () => headOn(61.5)));
    expect(Math.abs(await reversePull(six) - fresh)).toBeGreaterThan(.8);
    const cruising = await cruise(six);
    expect(Math.abs(cruising.rate)).toBeGreaterThan(.5); expect(Math.abs(cruising.rate)).toBeLessThan(4);
    expect(cruising.contact).toBe(true); expect(cruising.kmh).toBeGreaterThan(70);
  });
  test('a corner hit pulls towards that corner; its wheel mount moves back with the cage, as the drawn hub does', async () => {
    const left = wireOf([{ dv: [0, 0, -75 / 3.6], at: [.8, .5, 2.1], halfWidth: .2 }]);
    const m = mechanicsOf(left);
    expect(m.toe[0]).toBeGreaterThan(m.toe[1] > 0 ? m.toe[1] : -m.toe[1]);
    expect(m.steer).toBeGreaterThan(0);
    expect((await cruise(left)).rate).toBeGreaterThan(.3);
    expect((await cruise(wireOf([{ dv: [0, 0, -75 / 3.6], at: [-.8, .5, 2.1], halfWidth: .2 }]))).rate).toBeLessThan(-.3);
    // The physics mount follows the cage at the hub along and across the car, the drawn hub the same offset.
    const { pw, done } = await road(left);
    const { modelTuning } = await import('../../src/vehicle/modelTuning');
    const { DamageRig } = await import('../../src/actors/fleet/damageRig');
    const tuned = modelTuning(PLAYER_MODEL)!.wheels, c = pw.vehicle!.controller;
    const rig = new DamageRig(layout); rig.show(left);
    for (let i = 0; i < 4; i++) {
      const mount = c.wheelChassisConnectionPointCs(i)!, hub = rig.offsetAt(-tuned[i].z, modelTuning(PLAYER_MODEL)!.wheelRadius, tuned[i].x, new THREE.Vector3());
      expect(mount.x - tuned[i].x).toBeCloseTo(hub.z, 4); expect(mount.z - tuned[i].z).toBeCloseTo(-hub.x, 4);
      expect(mount.y).toBeCloseTo(tuned[i].y, 4);
    }
    // The hit front-left wheel moved back several centimetres, more than the front-right; the rear stayed.
    const back = (i: number) => tuned[i].x - c.wheelChassisConnectionPointCs(i)!.x;
    expect(back(0)).toBeGreaterThan(.05); expect(back(0)).toBeGreaterThan(back(1) + .02);
    expect(Math.abs(back(2)) + Math.abs(back(3))).toBeLessThan(.005);
    rig.release(); done();
  });
  test('a wrecked corner sags and softens; the car keeps all four wheels and stays drivable', async () => {
    // Side hits at the front-left wheel past the suspension ladder (wear 16000+): three at 85 km/h.
    const side: DamageHit = { dv: [-85 / 3.6, 0, 0], at: [1, .5, 1.3], halfWidth: .3 };
    const m = mechanicsOf(wireOf([side, side, side]));
    expect(m.suspension[0]).toBeGreaterThanOrEqual(1); expect(m.sag[0]).toBeGreaterThan(0); expect(m.damping[0]).toBeLessThan(1);
    const { pw, step, done } = await road(wireOf([side, side, side]));
    for (let i = 0; i < 60; i++) step();
    const state = pw.vehicleStates()[0], c = pw.vehicle!.controller;
    expect(c.wheelChassisConnectionPointCs(0)!.y - c.wheelChassisConnectionPointCs(3)!.y).toBeCloseTo(m.sag[0], 3);
    // The sagging corner sits lower: its wheel is drawn higher in the body.
    expect(state.wheels[0].pos.y).toBeGreaterThan(state.wheels[1].pos.y + m.sag[0] / 2);
    expect(state.wheels.every(w => w.contact)).toBe(true);
    done();
    const wrecked = await cruise(wireOf([side, side, side, ...Array.from({ length: 6 }, () => headOn(61.5)), { dv: [0, 0, 70 / 3.6] }]));
    expect(wrecked.contact).toBe(true); expect(wrecked.kmh).toBeGreaterThan(65); expect(Math.abs(wrecked.rate)).toBeLessThan(6);
  });
  test('a rough engine: steady when intact; misfires and hunting with damage, steadier under throttle', () => {
    const ticks = (rough: number, throttle: number) => Array.from({ length: 600 }, (_, i) => roughEngine(rough, throttle, i / 30));
    expect(ticks(0, 0).every(t => t.rate === 1 && t.gain === 1)).toBe(true);
    const misfires = (list: { gain: number }[]) => list.filter(t => t.gain < 1).length / list.length;
    const idle = ticks(1, 0), load = ticks(1, 1);
    expect(misfires(idle)).toBeGreaterThan(.2); expect(misfires(idle)).toBeLessThan(.4);
    expect(misfires(load)).toBeLessThan(misfires(idle));
    expect(Math.min(...idle.map(t => t.gain))).toBeCloseTo(.45, 2);
    expect(Math.max(...idle.map(t => t.rate)) - Math.min(...idle.map(t => t.rate))).toBeGreaterThan(.05);
    // The same tick gives the same answer (the mix may evaluate it more than once).
    expect(roughEngine(.5, .2, 12.34)).toEqual(roughEngine(.5, .2, 12.34));
  });
  test('engine-bay steam and smoke are sprites of the tyre smoke draw, white and dark, and stop when intact', () => {
    const fx = new TyreEffects(new THREE.Group(), 'high'), camera = new THREE.PerspectiveCamera();
    const smoke = fx.root.children.find(c => c.name === 'tyre-smoke') as THREE.InstancedMesh;
    const draws = fx.root.children.length, rot = { x: 0, y: 0, z: 0, w: 1 }, pos = { x: 10, y: 1, z: 5 };
    const opacity = smoke.geometry.getAttribute('tyreOpacity'), shade = smoke.geometry.getAttribute('tyreShade');
    const lit = () => Array.from({ length: opacity.count }, (_, i) => i).filter(i => opacity.getX(i) > 0);
    for (let i = 0; i < 30; i++) { fx.begin(1 / 30); fx.engineBay(0, pos, rot, .35, 0, 1 / 30); fx.end(camera); }
    const steam = lit();
    expect(steam.length).toBeGreaterThan(3);
    expect(steam.every(i => shade.getX(i) > 1)).toBe(true);
    const m = new THREE.Matrix4(), p = new THREE.Vector3();
    smoke.getMatrixAt(steam[0], m); p.setFromMatrixPosition(m);
    // Above the bonnet front, 1.45 m ahead of the chassis centre (+X forward).
    expect(p.x).toBeGreaterThan(11.4); expect(p.y).toBeGreaterThan(1.3);
    for (let i = 0; i < 60; i++) { fx.begin(1 / 30); fx.engineBay(0, pos, rot, 0, .8, 1 / 30); fx.end(camera); }
    expect(lit().some(i => shade.getX(i) < .5)).toBe(true);
    for (let i = 0; i < 120; i++) { fx.begin(1 / 30); fx.engineBay(0, pos, rot, 0, 0, 1 / 30); fx.end(camera); }
    expect(lit()).toHaveLength(0);
    expect(fx.root.children.length).toBe(draws);
    fx.dispose();
  });
});
