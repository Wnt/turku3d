import { expect, test, vi } from 'vitest';
import * as R from '@dimforge/rapier3d';
import { PhysicsWorld } from '../../src/physics/world';
import { GROUP } from '../../src/shared/col/materials';
import { SharedVehicles, vehicleFingerprint, vehiclePose } from '../../src/net/sharedVehicles';
import { MultiplayerClient } from '../../src/net/client';
import type { PhysicsClient } from '../../src/physics/client';
import type { Manifest } from '../../src/world/manifest';
import type { ParkedVehicles } from '../../src/world/parkedVehicles';
import type { VehicleState } from '../../src/physics/protocol';

const state: VehicleState = { id: 'player', kind: 'car', pos: { x: 170, y: 4.6, z: 250 },
  rot: { x: .6, y: 0, z: 0, w: .8 }, linvel: { x: 4, y: 0, z: 1 },
  speed: 4, forwardSpeed: 4, wheels: [], curbStrike: null };

test('shared snapshots preserve flips, chassis height and velocity; time/noise do not dirty resting cars', () => {
  const p = vehiclePose(state, 'sedan/red', 10);
  expect(p.H).toBeCloseTo(4); expect(p.N).toBe(-250); expect(p.vN).toBe(-1); expect(p.rot).toEqual(state.rot);
  expect(vehicleFingerprint({ ...p, t: 100, E: p.E + .001 })).toBe(vehicleFingerprint(p));
  expect(vehicleFingerprint({ ...p, E: p.E + .1 })).not.toBe(vehicleFingerprint(p));
});

test('remote kinematic sweep cannot launch a local car, but bounded server impulse changes its momentum', () => {
  const pw = new PhysicsWorld(R);
  pw.spawnVehicle({ x: 0, y: 2, z: 0 }, 0);
  pw.setRemotes([{ id: 'remote', pos: { x: -10, y: 2, z: 0 }, yaw: -Math.PI / 2, mode: 'truck' }]);
  pw.world.step();
  pw.setRemotes([{ id: 'remote', pos: { x: 10, y: 2, z: 0 }, yaw: -Math.PI / 2, mode: 'truck' }]);
  pw.world.step();
  expect(pw.vehicleState()!.linvel.x).toBeCloseTo(0, 5);
  pw.vehicleImpulse('player', { x: 1e8, y: 1e8, z: 0 });
  expect(pw.vehicleState()!.linvel.x).toBeCloseTo(8);
  expect(pw.vehicleState()!.linvel.y).toBeLessThan(0);
  expect(pw.vehicleState()!.impact?.impulse).toBeCloseTo(9600);
  pw.world.free();
});

test('a remote car proxy moves at its owner\'s speed: a pose catching up 1.1 m does not launch a car it touches', () => {
  const pw = new PhysicsWorld(R);
  pw.world.createCollider(R.ColliderDesc.cuboid(200, 1, 200).setTranslation(0, -1, 0).setCollisionGroups(0xffff0000 | 0xffff));
  pw.spawnVehicle({ x: 0, y: 1, z: 0 }, 0, { id: 'me', kind: 'car' });
  pw.driveVehicle('me');
  const idle = { vx: 0, vz: 0, jump: false }, push = { throttle: 0.3, steer: 0, brake: 0, handbrake: false };
  for (let k = 0; k < 60; k++) pw.fixedStep(idle, { ...push, throttle: 0 });
  const me = () => pw.vehicleState()!;
  // A resting shared car nose to nose with ours (chassis half lengths 2.1 + 2.1): its owner reports no velocity.
  let x = me().pos.x + 4.35;
  const resting = () => [{ id: 'van', pos: { x, y: me().pos.y, z: me().pos.z }, yaw: 0, rot: { x: 0, y: 0, z: 0, w: 1 }, mode: 'car' as const, vel: { x: 0, y: 0, z: 0 } }];
  for (let k = 0; k < 60; k++) { pw.setRemotes(resting()); pw.fixedStep(idle, push); }
  // Its pose catches up 1.1 m into us at once (verify-all r2: 1.6 -> 73.9 m/s in one step before the sweep limit).
  x -= 1.1; pw.setRemotes(resting());
  let fastest = 0;
  for (let k = 0; k < 60; k++) { pw.fixedStep(idle, push); fastest = Math.max(fastest, me().speed); }
  expect(fastest).toBeLessThan(2);
  // A remote car driving at 16.7 m/s, seen at 20 fps (three substeps per update): its proxy keeps up at that speed.
  pw.setRemotes([]);
  let rx = -40;
  const steps: number[] = [];
  let last = rx;
  for (let k = 0; k < 90; k++) {
    if (k % 3 === 0) { rx += 16.7 * 3 / 60; pw.setRemotes([{ id: 'driver', pos: { x: rx, y: 1, z: 30 }, yaw: 0, rot: { x: 0, y: 0, z: 0, w: 1 }, mode: 'car', vel: { x: 16.7, y: 0, z: 0 } }]); }
    pw.fixedStep(idle, null);
    const at = (pw as unknown as { remotes: Map<string, { body: R.RigidBody }> }).remotes.get('driver')!.body.translation().x;
    steps.push((at - last) * 60); last = at;
  }
  expect(Math.max(...steps.slice(3))).toBeLessThan(16.7 * 1.25 + 1 + 0.01);
  expect(rx - last).toBeLessThan(0.6);
  pw.world.free();
});

test('a local car cannot drive through a remote car: the proxy blocks the chassis, wheel rays ignore it', async () => {
  const fs = await import('node:fs'), path = await import('node:path');
  const dir = path.resolve(__dirname, '../../public/dev-tiles');
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
  const pw = new PhysicsWorld(R);
  for (const id of Object.keys(manifest.collision)) {
    const b = fs.readFileSync(path.join(dir, manifest.collision[id].url));
    await pw.loadTile(id, b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer);
  }
  pw.world.step();
  // Flat dev ground at y 2: the local car drives east at full throttle into a remote car parked 12 m ahead.
  pw.spawnVehicle({ x: 30, y: 2.8, z: 100 }, 0);
  for (let k = 0; k < 30; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
  // A remote car's proxy sits at its chassis centre (net/remotes.ts adds VEHICLE_POSE_HEIGHT to the network H).
  const remote = [{ id: 'remote', pos: { x: 42, y: pw.vehicleState()!.pos.y, z: 100 }, yaw: -Math.PI / 2, mode: 'car' as const }];
  for (let k = 0; k < 180; k++) { pw.setRemotes(remote); pw.fixedStep(null, { throttle: 1, steer: 0, brake: 0, handbrake: false }); }
  const car = pw.vehicleState()!;
  // Stopped short of it (chassis half lengths 2.1 + 2.1), not through it, and still on its wheels on the ground.
  expect(car.pos.x).toBeLessThan(42 - 3.5);
  expect(car.pos.y).toBeLessThan(3.2);
  expect(car.wheels.filter(w => w.contact).length).toBe(4);
  pw.world.free();
});

test('ownership transfer removes the old body and restores the new owner with full quaternion/velocity', () => {
  const client = new MultiplayerClient('ws://localhost:5403', null);
  client.state.connected = true; client.state.you = { id: 'self', nick: 'AA', spawn: { E: 170, N: -250, yaw: 0 } };
  const physics = { latest: { vehicles: [state] }, spawnVehicle: vi.fn(), removeVehicle: vi.fn(), vehicleImpulse: vi.fn(), setVehicleDamage: vi.fn(), ownVehicleDamage: vi.fn() };
  const parked = { setShared: vi.fn() };
  const shared = new SharedVehicles(client, physics as unknown as PhysicsClient, { vehicles: [] } as unknown as Manifest, parked as unknown as ParkedVehicles);
  const vehicle = { id: 'self:player', creator: 'self', localId: 'player', source: null, owner: 'self', occupant: null,
    epoch: 1, revision: 1, half: { x: 2.1, y: .3, z: .9 }, pose: vehiclePose(state, 'player', 10) };
  client.state.vehicles.set(vehicle.id, vehicle);
  shared.update(20); expect(physics.spawnVehicle).toHaveBeenCalledWith(state.pos, expect.any(Number), expect.objectContaining({ rot: state.rot }));
  const updates = shared.updates(performance.now() + 200); expect(updates).toHaveLength(1); shared.sentUpdates(updates);
  expect(shared.updates(performance.now() + 300)).toEqual([]);
  client.state.vehicles.set(vehicle.id, { ...vehicle, owner: 'other', epoch: 2 }); shared.sync();
  expect(physics.removeVehicle).toHaveBeenCalledWith('player');
  client.state.vehicles.set('foreign', { ...vehicle, id: 'foreign', creator: 'other', owner: 'self', epoch: 3 }); shared.sync();
  expect(physics.spawnVehicle).toHaveBeenCalledWith(state.pos, expect.any(Number), expect.objectContaining({ id: 'foreign', rot: state.rot, linvel: state.linvel }));
});

test('network restore preserves pitch/roll instead of yaw-resetting an empty vehicle', () => {
  const pw = new PhysicsWorld(R);
  pw.world.createCollider(R.ColliderDesc.cuboid(20, .5, 20).setTranslation(0, -.5, 0)
    .setCollisionGroups((GROUP.STATIC_SOLID << 16) | 0xffff));
  pw.spawnVehicle(state.pos, 0, { id: 'shared', kind: 'car', rot: state.rot, linvel: state.linvel });
  expect(pw.vehicleStates()[0].rot).toEqual(expect.objectContaining({ x: expect.closeTo(.6), w: expect.closeTo(.8) }));
  expect(pw.vehicleStates()[0].linvel).toEqual(state.linvel);
  pw.world.free();
});

test('a multiplayer frame asks each shared record its local id once, not once per physics car', () => {
  const client = new MultiplayerClient('ws://localhost:5403', null);
  client.state.connected = true; client.state.you = { id: 'self', nick: 'AA', spawn: { E: 170, N: -250, yaw: 0 } };
  // City play: ~150 parked and traffic bodies near the player, ~40 room records, a few thousand world cars.
  const cars = Array.from({ length: 150 }, (_, i) => ({ ...state, id: `car${i}`, pos: { x: i, y: 4.6, z: 250 } }));
  const world = Array.from({ length: 3000 }, (_, i) => ({ object_id: `car${i}`, model: 'sedan', x: i, y: 4.6, z: 250, yaw: 0 }));
  const physics = { latest: { vehicles: cars }, spawnVehicle: vi.fn(), removeVehicle: vi.fn(), vehicleImpulse: vi.fn(), setVehicleDamage: vi.fn(), ownVehicleDamage: vi.fn() };
  const shared = new SharedVehicles(client, physics as unknown as PhysicsClient, { vehicles: world } as unknown as Manifest, { setShared: vi.fn() } as unknown as ParkedVehicles);
  for (let i = 0; i < 40; i++) client.state.vehicles.set(`other:${i}`, { id: `other:${i}`, source: `car${1000 + i}`, creator: 'other', localId: `car${1000 + i}`,
    owner: 'other', occupant: null, epoch: 1, revision: 1, half: { x: 2, y: .4, z: .9 },
    pose: { t: 1, E: 1000 + i, N: -250, H: 4, yaw: 0, vE: 0, vN: 0, vH: 0, mode: 'car', vehicle: 'sedan', carrying: false } } as never);
  const localId = vi.spyOn(shared, 'localId');
  shared.update(0);
  // Before: record(id) per physics car spread the room and asked every record (150 x 40 = 6,000 calls per frame).
  expect(localId.mock.calls.length).toBeLessThan(150);
});
