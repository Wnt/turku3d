import { expect, test } from 'vitest';
import * as RAPIER from '@dimforge/rapier3d';
import { PhysicsWorld } from '../../src/physics/world';
import { GROUP } from '../../src/shared/col/materials';
import { groups } from '../../src/shared/col/rapierBuild';

/** A 60 m hillside slab tilted about X (rising towards -Z), and a spawn point just above its middle. */
function hill(pw: PhysicsWorld, deg: number, x: number) {
  const half = deg * Math.PI / 360;
  pw.world.createCollider(RAPIER.ColliderDesc.cuboid(30, 1, 30).setTranslation(x, 0, 0)
    .setRotation({ x: Math.sin(half), y: 0, z: 0, w: Math.cos(half) })
    .setCollisionGroups(groups(GROUP.STATIC_SOLID, GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC | 0xff00)));
  return { x, y: 1 / Math.cos(2 * half) + 0.05, z: 0 };
}
const idle = { vx: 0, vz: 0, jump: false };
const distance = (a: { x: number; y: number; z: number }, b: typeof a) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);

test('an idle character holds its place on walkable hillsides and still walks, slides and jumps', () => {
  const pw = new PhysicsWorld(RAPIER);
  const spots = [8, 15, 25, 35].map((deg, i) => [deg, hill(pw, deg, 100 * i)] as const);
  const steep = hill(pw, 60, 1000);
  pw.world.step();
  for (const [deg, at] of spots) {
    pw.spawnCharacter(at);
    for (let k = 0; k < 120; k++) pw.fixedStep(idle, null);
    const rest = pw.characterState()!;
    expect(rest.grounded).toBe(true);
    let worst = 0;
    for (let k = 0; k < 600; k++) { pw.fixedStep(idle, null); worst = Math.max(worst, distance(pw.characterState()!.pos, rest.pos)); }
    // Rapier's grounded push used to creep 8-40 mm downhill in these 10 s, with up to 5 mm pops.
    expect(worst, `${deg} degrees`).toBe(0);
    for (let k = 0; k < 60; k++) pw.fixedStep({ vx: 0, vz: -3, jump: false }, null); // 1 s walk across the slope's rise
    expect(distance(pw.characterState()!.pos, rest.pos), `${deg} degrees walk`).toBeGreaterThan(1.5);
    pw.fixedStep({ vx: 0, vz: 0, jump: true }, null);
    for (let k = 0; k < 10; k++) pw.fixedStep(idle, null);
    expect(pw.characterState()!.grounded, `${deg} degrees jump`).toBe(false);
  }
  // Steeper than the climb limit: no hold, the character goes down.
  pw.spawnCharacter(steep);
  for (let k = 0; k < 30; k++) pw.fixedStep(idle, null);
  const start = pw.characterState()!.pos;
  for (let k = 0; k < 120; k++) pw.fixedStep(idle, null);
  expect(start.y - pw.characterState()!.pos.y).toBeGreaterThan(0.3);
});
