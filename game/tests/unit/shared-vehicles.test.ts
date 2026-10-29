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

test('ownership transfer removes the old body and restores the new owner with full quaternion/velocity', () => {
  const client = new MultiplayerClient('ws://localhost:5403', null);
  client.state.connected = true; client.state.you = { id: 'self', nick: 'AA', spawn: { E: 170, N: -250, yaw: 0 } };
  const physics = { latest: { vehicles: [state] }, spawnVehicle: vi.fn(), removeVehicle: vi.fn(), vehicleImpulse: vi.fn() };
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
