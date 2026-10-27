import { expect, test } from 'vitest';
import * as Rapier from '@dimforge/rapier3d';
import { PhysicsWorld } from '../../src/physics/world';

test('a sleeping parked vehicle can be entered, driven 20 m, and left in place', () => {
  const physics = new PhysicsWorld(Rapier);
  physics.world.createCollider(Rapier.ColliderDesc.cuboid(100, 0.5, 100).setTranslation(0, -0.5, 0));
  physics.spawnVehicle({ x: -15, y: 0.75, z: 0 }, 0);
  physics.spawnVehicle({ x: 0, y: 0.75, z: 0 }, 0,
    { id: 'parked/suv', parked: true, mass: 1700, halfExt: { x: 2.2, y: 0.5, z: 0.93 } });
  const parked = physics.vehicleStates().find(v => v.id === 'parked/suv')!;
  expect(parked.pos.x).toBe(0);
  physics.driveVehicle('parked/suv');
  const input = { throttle: 1, steer: 0, brake: 0, handbrake: false };
  for (let i = 0; i < 7 * 60; i++) physics.fixedStep({ vx: 0, vz: 0, jump: false }, input);
  const driven = physics.vehicleState()!;
  expect(driven.pos.x).toBeGreaterThan(20);
  expect(physics.vehicleStates().find(v => v.id === 'player')!.pos.x).toBeCloseTo(-15, 1);
  physics.driveVehicle(null);
  for (let i = 0; i < 4 * 60; i++) physics.fixedStep({ vx: 0, vz: 0, jump: false }, null);
  expect(physics.vehicleStates().find(v => v.id === 'parked/suv')!.pos.x).toBeGreaterThan(20);
});
