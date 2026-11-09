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

test('a remote car proxy placed past a local car cannot launch it; a bounded server impulse moves it after its hold', () => {
  const pw = new PhysicsWorld(R);
  pw.spawnVehicle({ x: 0, y: 2, z: 0 }, 0);
  pw.setRemotes([{ id: 'remote', pos: { x: -10, y: 2, z: 0 }, yaw: -Math.PI / 2, mode: 'truck' }]);
  pw.world.step();
  pw.setRemotes([{ id: 'remote', pos: { x: 10, y: 2, z: 0 }, yaw: -Math.PI / 2, mode: 'truck' }]);
  pw.world.step();
  expect(pw.vehicleState()!.linvel.x).toBeCloseTo(0, 5);
  pw.vehicleImpulse('player', { x: 1e8, y: 1e8, z: 0 });
  // Held IMPULSE_HOLD_S (9 substeps) in case this client's own solver has the same contact, then applied once.
  pw.fixedStep(null, null);
  expect(pw.vehicleState()!.linvel.x).toBeCloseTo(0, 5);
  for (let k = 0; k < 9; k++) pw.fixedStep(null, null);
  expect(pw.vehicleState()!.linvel.x).toBeCloseTo(8, 0);
  expect(pw.vehicleState()!.linvel.y).toBeLessThan(0);
  expect(pw.vehicleState()!.impact?.impulse).toBeCloseTo(9600);
  pw.world.free();
});

test('a remote car proxy moves at its owner\'s speed: a pose catching up 1.1 m does not launch a car it touches', () => {
  const pw = new PhysicsWorld(R);
  pw.world.createCollider(R.ColliderDesc.cuboid(200, 1, 200).setTranslation(0, -1, 0).setCollisionGroups(((GROUP.STATIC_SOLID & 0xffff) << 16) | 0xffff));
  pw.spawnVehicle({ x: 0, y: 1, z: 0 }, 0, { id: 'me', kind: 'car' });
  pw.driveVehicle('me');
  const idle = { vx: 0, vz: 0, jump: false }, push = { throttle: 0.3, steer: 0, brake: 0, handbrake: false };
  for (let k = 0; k < 60; k++) pw.fixedStep(idle, { ...push, throttle: 0 });
  const me = () => pw.vehicleState()!;
  // A resting shared car nose to nose with ours (chassis half lengths 2.1 + 2.1): its owner reports no velocity.
  let x = me().pos.x + 4.35;
  const resting = () => [{ id: 'van', pos: { x, y: me().pos.y, z: me().pos.z }, yaw: 0, rot: { x: 0, y: 0, z: 0, w: 1 }, mode: 'car' as const, vel: { x: 0, y: 0, z: 0 } }];
  for (let k = 0; k < 60; k++) { pw.setRemotes(resting()); pw.fixedStep(idle, push); }
  // Its pose catches up 1.1 m into us at once (verify-all r2: 1.6 -> 73.9 m/s in one step before the sweep limit). The
  // proxy follows at most at its owner's speed plus slack (or is placed, and the overlap pushed out gently).
  x -= 1.1; pw.setRemotes(resting());
  let fastest = 0;
  for (let k = 0; k < 60; k++) { pw.fixedStep(idle, push); fastest = Math.max(fastest, me().speed); }
  expect(fastest).toBeLessThan(4);
  // A small catch-up (0.2 m) is swept at the resting car's 1 m/s slack, never as one substep's jump.
  for (let k = 0; k < 60; k++) { pw.setRemotes(resting()); pw.fixedStep(idle, push); }
  x -= 0.2; pw.setRemotes(resting());
  fastest = 0;
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

test('a local car cannot drive through a remote car: the proxy stops the chassis by its mass, wheel rays ignore it', async () => {
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
  const proxy = () => (pw as unknown as { remotes: Map<string, { body: R.RigidBody }> }).remotes.get('remote')!.body.translation();
  let closest = Infinity;
  for (let k = 0; k < 180; k++) {
    pw.setRemotes(remote); pw.fixedStep(null, { throttle: 1, steer: 0, brake: 0, handbrake: false });
    closest = Math.min(closest, proxy().x - pw.vehicleState()!.pos.x);
  }
  const car = pw.vehicleState()!;
  // Never through it (chassis half lengths 2.1 + 2.1, less Rapier's contact skin), still on its wheels on the ground.
  expect(closest).toBeGreaterThan(4.2 - 0.15);
  expect(car.pos.y).toBeLessThan(3.2);
  expect(car.wheels.filter(w => w.contact).length).toBe(4);
  pw.world.free();
});

/** Flat ground (static membership: remote proxies never touch it) and a local car 'me' at x 0 facing +x. */
function flatWorld() {
  const pw = new PhysicsWorld(R);
  pw.world.createCollider(R.ColliderDesc.cuboid(300, 1, 300).setTranslation(0, -1, 0).setCollisionGroups(((GROUP.STATIC_SOLID & 0xffff) << 16) | 0xffff));
  pw.spawnVehicle({ x: 0, y: 1, z: 0 }, 0, { id: 'me', kind: 'car' });
  pw.driveVehicle('me');
  for (let k = 0; k < 60; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
  return pw;
}

test('a remote car has its mass: a local car hitting a resting one of the same class keeps about half its speed', () => {
  // Jonni on city.lab 2026-11-09: "the bots feel rock solid" (a kinematic proxy stopped the car like a wall).
  const pw = flatWorld(), me = () => pw.vehicleState()!;
  const remote = [{ id: 'bot', pos: { x: 40, y: me().pos.y, z: 0 }, yaw: 0, rot: { x: 0, y: 0, z: 0, w: 1 }, mode: 'car' as const, vel: { x: 0, y: 0, z: 0 } }];
  const proxy = () => (pw as unknown as { remotes: Map<string, { body: R.RigidBody }> }).remotes.get('bot')!.body.translation();
  let before = 0, hit = -1;
  for (let k = 0; k < 600 && hit < 0; k++) {
    pw.setRemotes(remote); pw.fixedStep(null, { throttle: 1, steer: 0, brake: 0, handbrake: false });
    if (proxy().x - me().pos.x < 4.25) hit = k; else before = me().linvel.x;
  }
  expect(hit).toBeGreaterThan(0);
  expect(before).toBeGreaterThan(6);
  for (let k = 0; k < 6; k++) { pw.setRemotes(remote); pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: false }); }
  // Momentum split by the two masses (restitution 0.05): neither a wall's dead stop nor a pass-through.
  expect(me().linvel.x).toBeGreaterThan(before * 0.25);
  expect(me().linvel.x).toBeLessThan(before * 0.75);
  expect(proxy().x).toBeGreaterThan(40.3);
  // Like a shoved parked car, not a spring (Jonni: "way too springy"). Its owner takes the same hit on its own client and
  // reports it 200 ms later (here: the proxy's own path, delayed). The car only slides on and stops, drawn where it is,
  // never back towards its pre-hit pose, and then follows its owner again.
  const drawn = () => pw.remoteProxies().find(r => r.id === 'bot');
  expect(drawn()).toBeDefined();
  const path: number[] = [];
  let x = proxy().x, backwards = 0;
  for (let k = 0; k < 240; k++) {
    path.push(proxy().x);
    const owner = path.length > 12 ? path[path.length - 13] : 40;
    pw.setRemotes([{ ...remote[0], pos: { ...remote[0].pos, x: owner } }]);
    pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
    backwards = Math.max(backwards, x - proxy().x); x = proxy().x;
  }
  expect(backwards).toBeLessThan(0.005);
  expect(proxy().x).toBeGreaterThan(41);
  expect(drawn()).toBeUndefined();
  pw.world.free();
});

test('a car its owner drives away follows its owner even while a local car leans on it', () => {
  const pw = flatWorld(), me = () => pw.vehicleState()!;
  const proxy = () => (pw as unknown as { remotes: Map<string, { body: R.RigidBody }> }).remotes.get('bot')!.body.translation();
  let ox = me().pos.x + 4.3;
  const at = () => [{ id: 'bot', pos: { x: ox, y: me().pos.y, z: 0 }, yaw: 0, rot: { x: 0, y: 0, z: 0, w: 1 }, mode: 'car' as const, vel: { x: 0, y: 0, z: 0 } }];
  for (let k = 0; k < 90; k++) { pw.setRemotes(at()); pw.fixedStep(null, { throttle: 0.3, steer: 0, brake: 0, handbrake: false }); }
  // Pulls away at 5 m/s for two seconds.
  for (let k = 0; k < 120; k++) {
    ox += 5 / 60;
    pw.setRemotes([{ ...at()[0], vel: { x: 5, y: 0, z: 0 } }]); pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
  }
  expect(Math.abs(proxy().x - ox)).toBeLessThan(0.5);
  pw.world.free();
});

test('a server impulse for a contact this client solved is dropped; one for a contact it never saw moves the car', () => {
  const pw = flatWorld(), me = () => pw.vehicleState()!;
  // A resting remote car just ahead (chassis half lengths 2.1 + 2.1); ours rolls into it.
  const remote = [{ id: 'bot', pos: { x: me().pos.x + 4.4, y: me().pos.y, z: 0 }, yaw: 0, rot: { x: 0, y: 0, z: 0, w: 1 }, mode: 'car' as const, vel: { x: 0, y: 0, z: 0 } }];
  const touched = () => (pw as unknown as { remoteTouch: Map<string, number> }).remoteTouch.has('me');
  for (let k = 0; k < 120 && !touched(); k++) { pw.setRemotes(remote); pw.fixedStep(null, { throttle: 0.4, steer: 0, brake: 0, handbrake: false }); }
  expect(touched()).toBe(true);
  // The server's impulse for this contact arrives now and must not push the car a second time.
  pw.vehicleImpulse('me', { x: -12000, y: 0, z: 0 });
  for (let k = 0; k < 20; k++) { pw.setRemotes(remote); pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true }); }
  expect(me().linvel.x).toBeGreaterThan(-1);
  // Far from every remote car for over IMPULSE_DEDUP_S: the server's impulse is this client's only news of a hit.
  pw.setRemotes([]);
  for (let k = 0; k < 60; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: true });
  pw.vehicleImpulse('me', { x: -6000, y: 0, z: 0 });
  for (let k = 0; k < 10; k++) pw.fixedStep(null, { throttle: 0, steer: 0, brake: 0, handbrake: false });
  expect(me().linvel.x).toBeLessThan(-2);
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

test('a start car placed away from its boot spot stays local until it moves from there', () => {
  const client = new MultiplayerClient('ws://localhost:5403', null);
  client.state.connected = true; client.state.you = { id: 'self', nick: 'AA', spawn: { E: 170, N: -250, yaw: 0 } };
  const still = { ...state, linvel: { x: 0, y: 0, z: 0 }, speed: 0, forwardSpeed: 0 };
  const physics = { latest: { vehicles: [still] }, spawnVehicle: vi.fn(), removeVehicle: vi.fn(), vehicleImpulse: vi.fn(), setVehicleDamage: vi.fn(), ownVehicleDamage: vi.fn() };
  const shared = new SharedVehicles(client, physics as unknown as PhysicsClient, { vehicles: [] } as unknown as Manifest, { setShared: vi.fn() } as unknown as ParkedVehicles);
  const register = vi.spyOn(client, 'vehicle').mockResolvedValue(undefined as never);
  shared.update(0); // first sight at the boot spot
  // The start search parks it 40 m away (a start pair): no registration, also while the snapshot still lags.
  shared.starterPlaced({ x: 210, y: 4.6, z: 250 });
  shared.update(performance.now());
  physics.latest.vehicles = [{ ...still, pos: { x: 210, y: 4.6, z: 250 } }];
  shared.update(performance.now() + 2000);
  expect(register).not.toHaveBeenCalled();
  // Driven away from there, it is shared.
  physics.latest.vehicles = [{ ...still, pos: { x: 214, y: 4.6, z: 250 }, linvel: { x: 4, y: 0, z: 0 } }];
  shared.update(performance.now() + 3000);
  expect(register).toHaveBeenCalled();
});
