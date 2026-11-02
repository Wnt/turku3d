import { expect, test } from 'vitest';
import * as R from '@dimforge/rapier3d';
import { VehicleSignals } from '../../src/vehicle/signals';
import { DEFAULT_BINDINGS, mapActions } from '../../src/input/bindings';
import { PhysicsWorld } from '../../src/physics/world';
import { vehicleFingerprint, vehiclePose } from '../../src/net/sharedVehicles';
import { shouldSendPose } from '../../src/net/client';
import { cleanPose, validPose } from '../../../server/pose.js';

test('entry starts driving lights; three states cycle and menus suppress both actions', () => {
  const signals = new VehicleSignals(); signals.enter();
  expect(signals.lights).toBe(1);
  signals.update(true, true, true); expect(signals.lights).toBe(2); expect(signals.horn).toBe(true);
  signals.update(false, true, true); expect(signals.lights).toBe(2); expect(signals.horn).toBe(false);
  signals.update(true, true, false); expect(signals.lights).toBe(0);
  signals.update(true, true, false); expect(signals.lights).toBe(1);
  signals.enter(); expect(signals.lights).toBe(1);
});

test('keyboard and Xbox bindings leave copy position independent', () => {
  const keys = mapActions(null, new Set(['KeyG', 'KeyL']), DEFAULT_BINDINGS);
  expect([keys.horn, keys.lights, keys.copyPosition]).toEqual([1, 1, 0]);
  expect(mapActions(null, new Set(['KeyJ']), DEFAULT_BINDINGS).copyPosition).toBe(1);
  const buttons = Array.from({ length: 16 }, (_, i) => ({ value: i === 10 || i === 12 ? 1 : 0 }));
  const pad = mapActions({ mapping: 'standard', connected: true, axes: [0,0,0,0], buttons } as unknown as Gamepad, new Set(), DEFAULT_BINDINGS);
  expect([pad.horn, pad.lights, pad.menuUp]).toEqual([1, 1, 1]);
});

test('all physics vehicle classes default on entry and retain signals through server snapshots', () => {
  const world = new PhysicsWorld(R);
  try {
    for (const kind of ['car', 'truck', 'scooter'] as const) {
      world.spawnVehicle({ x: 0, y: 1, z: 0 }, 0, { id: kind, kind, parked: true });
      world.driveVehicle(kind); expect(world.vehicleState()!.lights).toBe(1);
      world.fixedStep({ vx: 0, vz: 0, jump: false }, { throttle: 0, steer: 0, brake: 0, handbrake: true, lights: 2, horn: true });
      const pose = vehiclePose(world.vehicleState()!, kind === 'car' ? 'player' : kind, 100);
      expect(validPose(pose)).toBe(true);
      const wire = cleanPose(JSON.parse(JSON.stringify(pose)));
      expect([wire.lights, wire.horn]).toEqual([2, true]);
      expect(validPose({ ...wire, lights: 3 })).toBe(false);
      expect(validPose({ ...wire, horn: 1 })).toBe(false);
      const off = { ...pose, lights: 0 as const, horn: false };
      expect(vehicleFingerprint(pose)).not.toBe(vehicleFingerprint(off));
      expect(shouldSendPose(200, off, 100, pose)).toBe(true);
      world.driveVehicle(null); expect(world.vehicleStates().find(s => s.id === kind)!.horn).toBe(false);
    }
  } finally { world.world.free(); }
});

test('sleeping pose caches publish lamp changes and horn release without waking the chassis', () => {
  const world = new PhysicsWorld(R);
  try {
    world.spawnVehicle({ x: 0, y: 1, z: 0 }, 0, { parked: true });
    world.vehicleStates();
    world.vehicle!.lights = 2; world.vehicle!.horn = true;
    const on = world.vehicleStates()[0];
    expect([on.lights, on.horn]).toEqual([2, true]);
    expect(world.vehicleStates()[0]).toBe(on);
    world.vehicle!.lights = 0; world.vehicle!.horn = false;
    expect([world.vehicleStates()[0].lights, world.vehicleStates()[0].horn]).toEqual([0, false]);
    expect(world.vehicle!.body.isSleeping()).toBe(true);
  } finally { world.world.free(); }
});
