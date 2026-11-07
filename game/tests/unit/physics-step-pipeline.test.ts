import { expect, test, vi } from 'vitest';
import * as RAPIER from '@dimforge/rapier3d';
import { PhysicsWorld, stepPipeline } from '../../src/physics/world';
import { GROUP } from '../../src/shared/col/materials';
import { groups } from '../../src/shared/col/rapierBuild';

const idle = { vx: 0, vz: 0, jump: false };

test('a physics step does not walk every collider of the world (Rapier World.step soft-body wrapping)', () => {
  const pw = new PhysicsWorld(RAPIER);
  const solid = groups(GROUP.STATIC_SOLID, GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC | 0xff00);
  pw.world.createCollider(RAPIER.ColliderDesc.cuboid(200, 1, 200).setTranslation(0, -1, 0).setCollisionGroups(solid));
  // A few resident cells' primitives (poles, bollards, kerbs): thousands of small static colliders.
  for (let i = 0; i < 3000; i++) pw.world.createCollider(RAPIER.ColliderDesc.cuboid(0.1, 0.5, 0.1)
    .setTranslation(-150 + (i % 60) * 5, 0.5, 20 + Math.floor(i / 60) * 2).setCollisionGroups(solid));
  pw.spawnCharacter({ x: 0, y: 3, z: 0 });
  const wrap = vi.spyOn(pw.world, 'mapNewSoftBodies');
  for (let k = 0; k < 90; k++) pw.fixedStep(idle, null);
  expect(wrap).not.toHaveBeenCalled();
  expect(pw.characterState()!.grounded).toBe(true);
  expect(pw.characterState()!.pos.y).toBeLessThan(1.5);
  // The same solver step as World.step, without its per-step walk: a dropped box comes to rest on the ground.
  const box = pw.world.createRigidBody(RAPIER.RigidBodyDesc.dynamic().setTranslation(10, 4, 0));
  pw.world.createCollider(RAPIER.ColliderDesc.cuboid(0.5, 0.5, 0.5), box);
  for (let k = 0; k < 180; k++) stepPipeline(pw.world);
  expect(box.translation().y).toBeGreaterThan(0.3);
  expect(box.translation().y).toBeLessThan(0.7);
  expect(pw.world.getCollider(box.collider(0).handle)).toBeDefined();
});
