import './fleet-assets.fixture';
import { describe, expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { applyHit, DamageState, damageLayout, decodeDamage, encodeDamage, GLASS_STAGE, PART_STATE, PARTS, SURFACE_SIZE, surfaceCell, WINDOWS, type PartName, type WindowName } from '../../src/vehicle/damage';
import { fleetDamageBox, FLEET_BY_ID, PLAYER_MODEL } from '../../src/actors/fleet/catalog';
import { loadAuthoredFleet, loadAuthoredFleetParts } from '../../src/actors/fleet/authored';
import { DamageRig, modelLayout, onPartDetached, onWindowShattered, skinToCage, type DetachEvent, type ShatterEvent } from '../../src/actors/fleet/damageRig';
import { partsOf, windowsOf } from '../../src/actors/fleet/damageParts';

const golf = FLEET_BY_ID.get(PLAYER_MODEL)!;
const layout = damageLayout(fleetDamageBox(golf));
const part = (state: DamageState, name: PartName) => state.parts[PARTS.indexOf(name)];
const headOn = (state: DamageState, kmh: number) => applyHit(state, { dv: [0, 0, -kmh / 3.6] });

describe('loose and detached parts (vehicle/damage.ts PART_LOAD)', () => {
  test('bumpers: nothing comes off in a single hit up to 85 km/h (Wreckfest 2); loose after 3 at 60, off after 5 or one at 90', () => {
    for (const kmh of [50, 70, 84, 85]) {
      const s = new DamageState(layout); headOn(s, kmh);
      expect(part(s, 'frontBumper'), `${kmh} km/h`).toBeLessThan(PART_STATE.detached);
    }
    const s = new DamageState(layout), states: number[] = [];
    for (let i = 0; i < 6; i++) { headOn(s, 60); states.push(part(s, 'frontBumper')); }
    expect(states).toEqual([0, 0, 1, 1, 2, 2]);
    // The rear bumper only takes rear hits.
    expect(part(s, 'rearBumper')).toBe(0);
    const fast = new DamageState(layout); headOn(fast, 90);
    expect(part(fast, 'frontBumper')).toBe(PART_STATE.detached);
    const one75 = new DamageState(layout); headOn(one75, 75);
    expect(part(one75, 'frontBumper')).toBe(PART_STATE.loose);
  });
  test('bonnet and boot lid: the latch after 4 hits at 60 or one at 81 km/h, off after 7 at 60; normal driving knocks nothing', () => {
    const s = new DamageState(layout), bonnet: number[] = [];
    for (let i = 0; i < 7; i++) { headOn(s, 60); bonnet.push(part(s, 'bonnet')); }
    expect(bonnet).toEqual([0, 0, 0, 1, 1, 1, 2]);
    const rear = new DamageState(layout); applyHit(rear, { dv: [0, 0, 82 / 3.6] });
    expect(part(rear, 'bootLid')).toBe(PART_STATE.loose);
    expect(part(rear, 'bonnet')).toBe(0);
    // Parking knocks and kerb taps: 20 hits at 30 km/h from every side leave every part on.
    const city = new DamageState(layout);
    for (let i = 0; i < 20; i++) for (const dv of [[0, 0, -30], [0, 0, 30], [12, 0, 0], [-12, 0, 0]] as const)
      applyHit(city, { dv: dv.map(v => v / 3.6) as [number, number, number] });
    expect(Array.from(city.parts)).toEqual([0, 0, 0, 0, 0, 0]);
  });
  test('mirrors go with side hits over the front two thirds: loose at 30, off at 40 km/h; a rear-quarter hit spares them', () => {
    const side = (kmh: number, z = .5, push = -1) => { const s = new DamageState(layout);
      applyHit(s, { dv: [push * kmh / 3.6, 0, 0], at: [-push, .6, z], halfWidth: .4 }); return s; };
    expect(part(side(30), 'mirrorLeft')).toBe(PART_STATE.loose);
    expect(part(side(30), 'mirrorRight')).toBe(0);
    expect(part(side(40), 'mirrorLeft')).toBe(PART_STATE.detached);
    expect(part(side(40, .5, 1), 'mirrorRight')).toBe(PART_STATE.detached);
    expect(part(side(40, -2), 'mirrorLeft')).toBe(0);
    // A light knock (no crumple at all) still counts as a hit: the state is sent.
    const knock = side(30);
    expect(knock.hits).toBe(1);
    expect(knock.offsets.every(v => v === 0)).toBe(true);
  });
  test('a corner hit frees the bumper end it struck; states, sides and loads round-trip in the wire (version 3)', () => {
    const s = new DamageState(layout);
    for (let i = 0; i < 3; i++) applyHit(s, { dv: [0, 0, -65 / 3.6], at: [.8, .6, 2.1], halfWidth: .2 });
    expect(part(s, 'frontBumper')).toBe(PART_STATE.loose);
    expect(s.partSide[PARTS.indexOf('frontBumper')]).toBe(1);
    const back = new DamageState(layout);
    expect(decodeDamage(encodeDamage(s), back)).toBe(true);
    expect(Array.from(back.parts)).toEqual(Array.from(s.parts));
    expect(Array.from(back.partSide)).toEqual(Array.from(s.partSide));
    back.partLoad.forEach((load, i) => expect(Math.abs(load - s.partLoad[i])).toBeLessThanOrEqual(4));
    // Detached stays detached; further hits do not move its load.
    const off = new DamageState(layout); headOn(off, 95);
    const load = off.partLoad[0]; headOn(off, 95);
    expect(off.partLoad[0]).toBe(load);
    // Version 3 (242 bytes) reads with intact glass, version 2 (224 bytes) with every part attached.
    const v3 = Uint8Array.from(atob(encodeDamage(s)), c => c.charCodeAt(0)).slice(0, 242); v3[0] = 3;
    const three = new DamageState(layout);
    expect(decodeDamage(btoa(String.fromCharCode(...v3)), three)).toBe(true);
    expect(Array.from(three.parts)).toEqual(Array.from(s.parts));
    expect(Array.from(three.glass)).toEqual([0, 0, 0, 0, 0, 0]);
    const v2 = Uint8Array.from(atob(encodeDamage(s)), c => c.charCodeAt(0)).slice(0, 224); v2[0] = 2;
    const old = new DamageState(layout);
    expect(decodeDamage(btoa(String.fromCharCode(...v2)), old)).toBe(true);
    expect(Array.from(old.parts)).toEqual([0, 0, 0, 0, 0, 0]);
    // A part state beyond detached is malformed.
    const bad = Uint8Array.from(atob(encodeDamage(s)), c => c.charCodeAt(0)); bad[224] = 3;
    expect(decodeDamage(btoa(String.fromCharCode(...bad)), old)).toBe(false);
  });
});

describe('the starter car is split into its parts by region (actors/fleet/damageParts.ts)', () => {
  test('bumpers, bonnet, boot lid and both mirrors, each where it belongs; no triangle lost, few vertices duplicated', async () => {
    const parts = (await loadAuthoredFleetParts(PLAYER_MODEL, 0))!, body = parts.body;
    const vertices = body.getAttribute('position').count, triangles = body.index!.count;
    skinToCage(body, modelLayout(golf));
    expect(body.index!.count).toBe(triangles);
    expect(body.getAttribute('position').count - vertices).toBeLessThan(200);
    const split = partsOf(body)!, L = golf.length / 2;
    const of = (name: PartName) => split[PARTS.indexOf(name)]!;
    for (const name of PARTS) expect(of(name), name).not.toBeNull();
    // Bumpers: low, at the ends, full width; bonnet ahead of the windscreen; boot lid at the tail; mirrors outboard.
    expect(of('frontBumper').min.z).toBeGreaterThan(L - .2 * golf.length);
    expect(of('frontBumper').max.y).toBeLessThan(.8);
    expect(of('frontBumper').max.x - of('frontBumper').min.x).toBeGreaterThan(1.5);
    expect(of('rearBumper').max.z).toBeLessThan(-L + .22 * golf.length);
    expect(of('bonnet').min.z).toBeGreaterThan(.5);
    expect(of('bonnet').max.y).toBeLessThan(1);
    expect(of('bootLid').max.z).toBeLessThan(-L + .15 * golf.length);
    expect(of('mirrorLeft').min.x).toBeGreaterThan(.7);
    expect(of('mirrorRight').max.x).toBeLessThan(-.7);
    for (const name of PARTS) expect(of(name).triangles.length / 3, name).toBeGreaterThan(name.startsWith('mirror') ? 50 : 300);
    // Every vertex of a part's triangles carries that part; the rest is the body.
    const tag = body.getAttribute('damagePart');
    PARTS.forEach((name, i) => { for (const v of of(name).triangles) expect(tag.getX(v)).toBe(i + 1); });
  });
  test('a loose part swings about its mount, a detached one collapses and is announced once, after the first state shown', async () => {
    const parts = (await loadAuthoredFleetParts(PLAYER_MODEL, 0))!, body = parts.body;
    const cage = modelLayout(golf);
    skinToCage(body, cage);
    const mesh = new THREE.Mesh(body), rig = new DamageRig(cage); rig.bind(mesh); mesh.updateMatrixWorld();
    const events: DetachEvent[] = [];
    onPartDetached(e => events.push(e));
    const split = partsOf(body)!, bumper = split[0]!, tag = body.getAttribute('damagePart');
    const vertex = Array.from(bumper.triangles).find(v => tag.getX(v) === 1)!, rest = new THREE.Vector3(), now = new THREE.Vector3();
    const state = new DamageState(cage);
    rig.show(null, 0);
    rig.deform(body, vertex, rest);
    // Loose: three corner hits on the car's left; the bumper's left end drops over the next frames.
    for (let i = 0; i < 3; i++) applyHit(state, { dv: [0, 0, -62 / 3.6], at: [.8, .6, 2.1], halfWidth: .3 });
    expect(state.parts[0]).toBe(PART_STATE.loose);
    const wire = encodeDamage(state);
    let low = Infinity;
    const end = Array.from(bumper.triangles).reduce((best, v) => body.getAttribute('position').getX(v) > body.getAttribute('position').getX(best) ? v : best);
    for (let t = 0; t <= 2000; t += 16) { rig.show(wire, t); low = Math.min(low, rig.deform(body, end, now).y); }
    expect(low).toBeLessThan(body.getAttribute('position').getY(end) - .05);
    expect(events).toHaveLength(0);
    // Off: one more hard hit; announced once, and its vertices collapse onto one point.
    applyHit(state, { dv: [0, 0, -86 / 3.6] });
    expect(state.parts[PARTS.indexOf('bonnet')]).toBe(PART_STATE.loose);
    const off = encodeDamage(state);
    rig.show(off, 2100); rig.show(off, 2116);
    expect(events.map(e => e.info.name)).toEqual(['frontBumper']);
    const a = rig.deform(body, bumper.triangles[0], new THREE.Vector3()), b = rig.deform(body, bumper.triangles[1], new THREE.Vector3());
    expect(a.distanceTo(b)).toBeLessThan(.02);
    // The debris copy is the part as drawn before it collapsed: real triangles, near the car's nose.
    const copy = rig.partGeometry(body, bumper, 0);
    copy.computeBoundingBox();
    expect(copy.boundingBox!.max.x - copy.boundingBox!.min.x).toBeGreaterThan(1);
    expect(copy.boundingBox!.max.z).toBeGreaterThan(1.6);
    // A car first drawn with parts already off (a remote car coming into view) announces nothing.
    const late = new DamageRig(cage); late.bind(new THREE.Mesh(body)); late.show(off, 0);
    expect(events).toHaveLength(1);
    onPartDetached(null); rig.release(); late.release();
  });
});

describe('debris bodies in the physics world (world.ts spawnDebris)', () => {
  test('a piece falls and rests on the ground, cars do not see it with their wheels, and it goes after its lifetime', async () => {
    const R = await import('@dimforge/rapier3d');
    const { PhysicsWorld } = await import('../../src/physics/world');
    const { DEBRIS, DT } = await import('../../src/physics/constants');
    const pw = new PhysicsWorld(R);
    pw.world.createCollider(R.ColliderDesc.cuboid(50, .5, 50).setTranslation(0, -.5, 0));
    const idle = { vx: 0, vz: 0, jump: false };
    pw.spawnDebris({ id: 'd1', pos: { x: 0, y: 1, z: 0 }, rot: { x: 0, y: 0, z: 0, w: 1 }, half: { x: .85, y: .15, z: .1 }, mass: 7,
      linvel: { x: 2, y: 1, z: 0 }, angvel: { x: 0, y: 1, z: 0 } });
    for (let i = 0; i < 180; i++) pw.fixedStep(idle, null);
    const [piece] = pw.debrisStates();
    expect(piece.id).toBe('d1');
    expect(piece.pos.y).toBeGreaterThan(.05); expect(piece.pos.y).toBeLessThan(.3);
    // The cap drops the oldest.
    for (let i = 2; i <= DEBRIS.cap + 1; i++) pw.spawnDebris({ id: `d${i}`, pos: { x: i, y: .5, z: 3 }, rot: { x: 0, y: 0, z: 0, w: 1 },
      half: { x: .1, y: .1, z: .1 }, mass: 1, linvel: { x: 0, y: 0, z: 0 }, angvel: { x: 0, y: 0, z: 0 } });
    expect(pw.debrisStates().map(d => d.id)).not.toContain('d1');
    expect(pw.debrisStates()).toHaveLength(DEBRIS.cap);
    for (let i = 0; i < DEBRIS.lifetimeS / DT + 2; i++) pw.fixedStep(idle, null);
    expect(pw.debrisStates()).toHaveLength(0);
    pw.world.free();
  });
});

const pane = (state: DamageState, name: WindowName) => state.glass[WINDOWS.indexOf(name)];
describe('glass (vehicle/damage.ts GLASS_LOAD)', () => {
  test('side glass breaks from side hits and rolls, not from frontal crashes; the windscreen stands about twice as much', () => {
    const side = (kmh: number, z = 0) => { const s = new DamageState(layout); applyHit(s, { dv: [-kmh / 3.6, 0, 0], at: [1, .6, z], halfWidth: .5 }); return s; };
    expect(pane(side(15), 'frontLeft')).toBe(GLASS_STAGE.intact);
    expect(pane(side(21), 'frontLeft')).toBe(GLASS_STAGE.cracked);
    expect(pane(side(36), 'frontLeft')).toBe(GLASS_STAGE.shattered);
    expect(pane(side(36), 'frontRight')).toBe(GLASS_STAGE.intact);
    expect(pane(side(36), 'windscreen')).toBeLessThan(GLASS_STAGE.crazed);
    // A narrow hit at the front door: the rear door's window only cracks.
    const pole = new DamageState(layout); applyHit(pole, { dv: [-36 / 3.6, 0, 0], at: [1, .6, .1], halfWidth: .3 });
    expect(pane(pole, 'frontLeft')).toBe(GLASS_STAGE.shattered);
    expect(pane(pole, 'rearLeft')).toBeLessThan(GLASS_STAGE.crazed);
    // Three 25 km/h side hits (the AI derby's struck car) shatter it.
    const derby = new DamageState(layout), stages: number[] = [];
    for (let i = 0; i < 3; i++) { applyHit(derby, { dv: [-25 / 3.6, 0, 0], at: [1, .6, 0], halfWidth: .5 }); stages.push(pane(derby, 'frontLeft')); }
    expect(stages).toEqual([1, 2, 3]);
    // Head-on crashes up to 84 km/h, six at 61.5: no glass breaks (Wreckfest 2's rear window at 140 g).
    const front = new DamageState(layout); applyHit(front, { dv: [0, 0, -84 / 3.6] });
    const six = new DamageState(layout); for (let i = 0; i < 6; i++) applyHit(six, { dv: [0, 0, -61.5 / 3.6] });
    for (const s of [front, six]) expect(Array.from(s.glass)).toEqual([0, 0, 0, 0, 0, 0]);
    // A roof landing (a roll): every window loads, the windscreen and rear window fully, the side glass half.
    const roll = new DamageState(layout); applyHit(roll, { dv: [0, -28 / 3.6, 0] });
    expect(pane(roll, 'windscreen')).toBe(GLASS_STAGE.cracked);
    expect(pane(roll, 'frontLeft')).toBe(GLASS_STAGE.cracked);
    const hard = new DamageState(layout); applyHit(hard, { dv: [0, -40 / 3.6, 0] });
    expect(pane(hard, 'windscreen')).toBe(GLASS_STAGE.shattered);
    // Stages round-trip in the wire.
    const back = new DamageState(layout);
    expect(decodeDamage(encodeDamage(derby), back)).toBe(true);
    expect(Array.from(back.glass)).toEqual(Array.from(derby.glass));
  });
  test('the Golf glazing has six windows found by facing, lamp covers stay unbroken; a shatter is announced once', async () => {
    const glass = (await loadAuthoredFleet(PLAYER_MODEL, 0, 'glass'))!, cage = modelLayout(golf);
    skinToCage(glass, cage, 'glass');
    const windows = windowsOf(glass)!, tag = glass.getAttribute('damagePart'), position = glass.getAttribute('position');
    expect(windows.map(w => w.window).sort()).toEqual([...WINDOWS].sort());
    const where = (name: WindowName) => { const id = WINDOWS.indexOf(name) + 1, box = { minZ: Infinity, maxZ: -Infinity, minX: Infinity, maxX: -Infinity };
      for (let i = 0; i < tag.count; i++) if (tag.getY(i) === id) { box.minZ = Math.min(box.minZ, position.getZ(i)); box.maxZ = Math.max(box.maxZ, position.getZ(i));
        box.minX = Math.min(box.minX, position.getX(i)); box.maxX = Math.max(box.maxX, position.getX(i)); }
      return box; };
    expect(where('windscreen').minZ).toBeGreaterThan(0);
    expect(where('rearWindow').maxZ).toBeLessThan(-1.4);
    expect(where('frontLeft').minX).toBeGreaterThan(.5);
    expect(where('frontLeft').minZ).toBeGreaterThan(where('rearLeft').maxZ - .05);
    expect(where('rearRight').maxX).toBeLessThan(-.5);
    // Lamp covers (low glass) belong to no window.
    for (let i = 0; i < tag.count; i++) if (position.getY(i) < .75) expect(tag.getY(i)).toBe(0);
    const events: ShatterEvent[] = [];
    onWindowShattered(e => events.push(e));
    const rig = new DamageRig(cage), body = new THREE.Mesh(new THREE.BufferGeometry()), panes = new THREE.Mesh(glass);
    rig.bind(body); rig.bind(panes);
    const state = new DamageState(cage);
    rig.show(null, 0);
    applyHit(state, { dv: [-36 / 3.6, 0, 0], at: [1, .6, .1], halfWidth: .3 });
    rig.show(encodeDamage(state), 16); rig.show(encodeDamage(state), 32);
    expect(events.map(e => WINDOWS[e.window])).toEqual(['frontLeft']);
    expect(events[0].centre.x).toBeGreaterThan(.5);
    onWindowShattered(null); rig.release();
  });
});

describe('surface wear (vehicle/damage.ts stampSurface)', () => {
  test('a hit scuffs its footprint, a hard one to bare metal; the cells round-trip in the wire (version 5)', () => {
    const s = new DamageState(layout);
    applyHit(s, { dv: [0, 0, -16 / 3.6], at: [.7, .5, 2.1], halfWidth: .2 });
    const corner = surfaceCell(layout, 'front', .7, .4, 2.1), other = surfaceCell(layout, 'front', -.7, .4, 2.1);
    expect(s.surfaceWear[corner]).toBeGreaterThan(.05);
    expect(s.surfaceWear[corner]).toBeLessThan(.2);
    expect(s.surfaceWear[other]).toBe(0);
    expect(s.hits).toBe(1);
    const side = new DamageState(layout); applyHit(side, { dv: [-60 / 3.6, 0, 0], at: [1, .6, .3], halfWidth: .4 });
    expect(side.surfaceWear[surfaceCell(layout, 'posX', 1, .6, .3)]).toBeGreaterThan(.8);
    expect(side.surfaceWear[surfaceCell(layout, 'negX', -1, .6, .3)]).toBe(0);
    // A roll onto the roof: the roof is scuffed and dirty.
    const roof = new DamageState(layout); applyHit(roof, { dv: [0, -20 / 3.6, 0] });
    const top = surfaceCell(layout, 'top', 0, 1.4, 0);
    expect(roof.surfaceWear[top]).toBeGreaterThan(.1); expect(roof.surfaceDirt[top]).toBeGreaterThan(.1);
    const back = new DamageState(layout);
    expect(decodeDamage(encodeDamage(side), back)).toBe(true);
    side.surfaceWear.forEach((w, i) => expect(Math.abs(back.surfaceWear[i] - w)).toBeLessThanOrEqual(1 / 30 + 1e-6));
    expect(encodeDamage(side)).toHaveLength(688);
    // The unwrap: 16 x 16 cells, every face in its own rows.
    expect(surfaceCell(layout, 'posX', 1, 0, -2.1)).toBe(0);
    expect(surfaceCell(layout, 'negX', -1, 1.38, 2.1)).toBe(8 * SURFACE_SIZE - 1);
    expect(surfaceCell(layout, 'rear', .99, 1.38, -2.1)).toBe(SURFACE_SIZE * SURFACE_SIZE - 1);
  });
  test('sliding along a wall wears the side that touches it; the wire follows at most twice a second', async () => {
    const R = await import('@dimforge/rapier3d');
    const { PhysicsWorld } = await import('../../src/physics/world');
    const { modelDamageBox, modelTuning } = await import('../../src/vehicle/modelTuning');
    const pw = new PhysicsWorld(R);
    pw.world.createCollider(R.ColliderDesc.cuboid(80, .5, 80).setTranslation(0, -.5, 0));
    // A wall along +X at z = 1.2 (the car faces +X, its right side, +Z in the chassis frame, towards the wall).
    pw.world.createCollider(R.ColliderDesc.cuboid(60, 1.5, .2).setTranslation(0, 1.5, 1.2 + .2));
    pw.spawnVehicle({ x: -20, y: .8, z: 0 }, 0, { tuning: modelTuning(PLAYER_MODEL), damage: { box: modelDamageBox(PLAYER_MODEL)! } });
    const idle = { vx: 0, vz: 0, jump: false };
    for (let i = 0; i < 60; i++) pw.fixedStep(idle, null);
    const car = pw.vehicle!, wires = new Set<string>();
    for (let i = 0; i < 120; i++) {
      car.body.setLinvel({ x: 12, y: car.body.linvel().y, z: .6 }, true);
      pw.fixedStep(idle, null);
      const wire = pw.vehicleStates()[0].damage;
      if (wire) wires.add(wire);
    }
    const d = car.damage!, cage = d.layout;
    const right = [...d.surfaceWear].slice(4 * SURFACE_SIZE, 8 * SURFACE_SIZE).reduce((a, b) => a + b), left = [...d.surfaceWear].slice(0, 4 * SURFACE_SIZE).reduce((a, b) => a + b);
    expect(right).toBeGreaterThan(.2);
    expect(left).toBe(0);
    expect(d.damaged).toBe(true);
    expect(d.hits).toBe(0);
    // Two seconds of scraping: a handful of wire updates, not one per step.
    expect(wires.size).toBeGreaterThan(0); expect(wires.size).toBeLessThanOrEqual(5);
    void cage;
    pw.world.free();
  });
});
