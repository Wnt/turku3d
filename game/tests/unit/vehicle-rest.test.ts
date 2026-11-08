import { expect, test } from 'vitest';
import * as R from '@dimforge/rapier3d';
import { PhysicsWorld } from '../../src/physics/world';
import { GROUP } from '../../src/shared/col/materials';
import { groups } from '../../src/shared/col/rapierBuild';
import { FLEET, PLAYER_MODEL } from '../../src/actors/fleet/catalog';
import { VEHICLES } from '../../src/physics/constants';
import { modelTuning } from '../../src/vehicle/modelTuning';

const idle = { vx: 0, vz: 0, jump: false };
const coast = { throttle: 0, steer: 0, brake: 0, handbrake: false };

test('a fleet car nobody drives falls asleep again once it stands still, and still moves when pushed', () => {
  const pw = new PhysicsWorld(R);
  pw.world.createCollider(R.ColliderDesc.cuboid(200, 1, 200).setTranslation(0, -1, 0).setCollisionGroups(groups(GROUP.STATIC_SOLID, 0xffff)));
  const tuning = modelTuning(PLAYER_MODEL)!;
  expect(tuning.tyre).toBeDefined();
  pw.spawnVehicle({ x: 20, y: 1.2, z: 0 }, 0, { id: 'parked', kind: 'car', parked: true, tuning });
  pw.spawnCharacter({ x: 50, y: 2, z: 50 });
  const body = (id: string) => (pw as unknown as { vehicles: Map<string, { body: R.RigidBody }> }).vehicles.get(id)!.body;
  for (let k = 0; k < 120; k++) pw.fixedStep(idle, coast);
  // The character walking past wakes it (world.ts nearbyVehicles); the tyre model's impulses keep its velocity at a few
  // cm/s, so Rapier's own velocity test never let it sleep again.
  body('parked').wakeUp();
  for (let k = 0; k < 120; k++) pw.fixedStep(idle, coast);
  expect(body('parked').isSleeping()).toBe(true);
  // Still a car: a push wakes it, it moves, and it settles again.
  body('parked').applyImpulse({ x: 3000, y: 0, z: 0 }, true);
  pw.fixedStep(idle, coast);
  const x0 = body('parked').translation().x;
  for (let k = 0; k < 10; k++) pw.fixedStep(idle, coast);
  expect(body('parked').translation().x).toBeGreaterThan(x0 + 0.05);
  for (let k = 0; k < 600; k++) pw.fixedStep(idle, coast);
  expect(body('parked').isSleeping()).toBe(true);
});

test('an asleep car wakes on its driver\'s throttle or steering, and its state says when it sleeps', () => {
  const pw = new PhysicsWorld(R);
  pw.world.createCollider(R.ColliderDesc.cuboid(200, 1, 200).setTranslation(0, -1, 0).setCollisionGroups(groups(GROUP.STATIC_SOLID, 0xffff)));
  pw.spawnVehicle({ x: 20, y: 1.2, z: 0 }, 0, { id: 'player', kind: 'car', tuning: modelTuning(PLAYER_MODEL)! });
  pw.spawnCharacter({ x: 50, y: 2, z: 50 });
  const body = (pw as unknown as { vehicles: Map<string, { body: R.RigidBody }> }).vehicles.get('player')!.body;
  for (let k = 0; k < 240; k++) pw.fixedStep(idle, null);
  expect(body.isSleeping()).toBe(true);
  expect(pw.vehicleStates().find(v => v.id === 'player')!.asleep).toBe(true);
  pw.driveVehicle('player'); body.sleep();
  pw.fixedStep(null, { ...coast, steer: 0.3 });
  expect(body.isSleeping()).toBe(false);
  expect(pw.vehicleStates().find(v => v.id === 'player')!.asleep).toBeUndefined();
});

test('a seated car is never put to rest, even while its driver sends no input; leaving it lets it sleep again', () => {
  const pw = new PhysicsWorld(R);
  pw.world.createCollider(R.ColliderDesc.cuboid(200, 1, 200).setTranslation(0, -1, 0).setCollisionGroups(groups(GROUP.STATIC_SOLID, 0xffff)));
  pw.spawnVehicle({ x: 20, y: 1.2, z: 0 }, 0, { id: 'player', kind: 'car', tuning: modelTuning(PLAYER_MODEL)! });
  pw.spawnCharacter({ x: 50, y: 2, z: 50 });
  const body = (pw as unknown as { vehicles: Map<string, { body: R.RigidBody }> }).vehicles.get('player')!.body;
  pw.driveVehicle('player');
  for (let k = 0; k < 240; k++) pw.fixedStep(null, null);
  expect(body.isSleeping()).toBe(false);
  pw.driveVehicle(null);
  for (let k = 0; k < 240; k++) pw.fixedStep(idle, null);
  expect(body.isSleeping()).toBe(true);
  // Braking alone wakes it too.
  pw.driveVehicle('player'); body.sleep();
  pw.fixedStep(null, { ...coast, brake: 1 });
  expect(body.isSleeping()).toBe(false);
});


test('every fleet model parked as the world spawns it stands on its wheels and drives off, the bus and the lorries too', () => {
  const pw = new PhysicsWorld(R);
  pw.world.createCollider(R.ColliderDesc.cuboid(200, 1, 2000).setTranslation(0, -1, 0).setCollisionGroups(groups(GROUP.STATIC_SOLID, 0xffff)));
  pw.spawnCharacter({ x: -150, y: 2, z: -150 });
  // Parked, claimed and shared fleet cars size the chassis box from the model (world/parkedVehicles.ts body).
  FLEET.forEach((m, i) => pw.spawnVehicle({ x: 0, y: 0.75, z: i * 25 }, 0, { id: m.id, kind: 'car', parked: true,
    halfExt: { x: m.length / 2, y: m.height * .28, z: m.width / 2 }, tuning: modelTuning(m.id)! }));
  for (let k = 0; k < 60; k++) pw.fixedStep(idle, coast);
  const vehicles = (pw as unknown as { vehicles: Map<string, { halfExt: { y: number } }> }).vehicles;
  const stuck: string[] = [];
  for (const m of FLEET) {
    const start = pw.vehicleStates().find(v => v.id === m.id)!.pos;
    pw.driveVehicle(m.id);
    for (let k = 0; k < 120; k++) pw.fixedStep(null, { ...coast, throttle: 1 });
    const end = pw.vehicleStates().find(v => v.id === m.id)!;
    if (Math.hypot(end.pos.x - start.x, end.pos.z - start.z) < 2 || end.wheels.filter(w => w.contact).length < 3) stuck.push(m.id);
    pw.driveVehicle(null);
  }
  expect(stuck).toEqual([]);
  expect(vehicles.get('transit-bus')!.halfExt.y).toBeLessThan(.6);
  // The built-in kinds keep their own boxes.
  for (const kind of ['car', 'truck', 'scooter'] as const) {
    pw.spawnVehicle({ x: 50, y: 2, z: 0 }, 0, { id: kind, kind });
    expect(vehicles.get(kind)!.halfExt).toEqual(VEHICLES[kind].halfExt);
  }
});

test('stability control stays out at a standstill: a bus whose resting slip angles drifted on a slope still moves off', () => {
  const pw = new PhysicsWorld(R);
  pw.world.createCollider(R.ColliderDesc.cuboid(200, 1, 200).setTranslation(0, -1, 0).setCollisionGroups(groups(GROUP.STATIC_SOLID, 0xffff)));
  pw.spawnCharacter({ x: 50, y: 2, z: 50 });
  pw.spawnVehicle({ x: 0, y: 0.75, z: 0 }, 0, { id: 'bus', kind: 'car', tuning: modelTuning('transit-bus')! });
  pw.driveVehicle('bus');
  for (let k = 0; k < 60; k++) pw.fixedStep(null, coast);
  type Esc = { cut: number; brake: number };
  const bus = (pw as unknown as { vehicles: Map<string, { body: R.RigidBody; tyreState: { lag: number[] }; stability(): Esc }> }).vehicles.get('bus')!;
  // As measured on the car show's 1 % lot (real collision, 2026-11-09): millimetre-per-second creep had relaxed the
  // slip angles to 65-81°, the rear's above the front's, and the control cut the drive and braked a front wheel.
  [-66, -65, -77, -81].forEach((deg, i) => { bus.tyreState.lag[i] = deg * Math.PI / 180; });
  bus.body.wakeUp();
  expect(bus.stability().cut).toBe(0);
  // Moving, the same slip angles are a slide the control catches.
  bus.body.setLinvel({ x: 10, y: 0, z: 0 }, true);
  expect(bus.stability().cut).toBeGreaterThan(0);
});
