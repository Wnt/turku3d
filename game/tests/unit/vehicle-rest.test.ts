import { expect, test } from 'vitest';
import * as R from '@dimforge/rapier3d';
import { PhysicsWorld } from '../../src/physics/world';
import { GROUP } from '../../src/shared/col/materials';
import { groups } from '../../src/shared/col/rapierBuild';
import { PLAYER_MODEL } from '../../src/actors/fleet/catalog';
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
