// The meshes come from the Python generators, not hand-written substitutes.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import RAPIER from '@dimforge/rapier3d-compat';
import { Walker } from '../src/kcc.ts';
import { materialClass, MAT, groups, GROUP } from '../../../game/src/shared/col/index.ts';

await RAPIER.init();
const repo = resolve(import.meta.dirname, '../../..');
let fixture = process.env.STRUCTURES_FIXTURE;
if (!fixture) {
  const work = process.env.TURKU3D_WORK;
  if (!work) throw new Error('Set TURKU3D_WORK or STRUCTURES_FIXTURE for isolated test output');
  fixture = join(work, 'build/structures-tests/scenes.json');
  mkdirSync(join(work, 'build/structures-tests'), { recursive: true });
  execFileSync('bash', [join(repo, 'pipeline/env/run.sh'), 'python', '-m', 'turku3d.structures.fixtures', '--out', fixture], { cwd: repo });
}
interface Scene {
  meshes: {positions: number[][]; indices: number[][]; material: number}[];
  meta: {top?: number[]; bottom?: number[]};
}
const scenes: Record<string, Scene> = JSON.parse(readFileSync(fixture, 'utf8'));

function worldFor(scene: Scene) {
  const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  world.createCollider(RAPIER.ColliderDesc.cuboid(20, 0.1, 10).setTranslation(0, -0.1, 0).setCollisionGroups(0x0001ffff));
  for (const m of scene.meshes) {
    const membership = materialClass(m.material) === 'query' ? GROUP.STATIC_QUERY : GROUP.STATIC_SOLID;
    world.createCollider(RAPIER.ColliderDesc.trimesh(new Float32Array(m.positions.flat()), new Uint32Array(m.indices.flat()))
      .setCollisionGroups(groups(membership, 0xffff)));
  }
  const top = scene.meta.top;
  if (top) world.createCollider(RAPIER.ColliderDesc.cuboid(3, 0.1, 2).setTranslation(top[0] + 3, top[1] - 0.1, 0).setCollisionGroups(0x0001ffff));
  world.step();
  return world;
}

for (const [name, scene] of Object.entries(scenes)) {
  test(`${name}: generated collision ${scene.meta.top ? 'climbs and descends' : 'blocks the character'}`, () => {
    const world = worldFor(scene);
    try {
      const walker = new Walker(RAPIER, world);
      walker.place(-1, 0, 0);
      const top = scene.meta.top;
      for (let i = 0; i < 600 && walker.x < 5; i++) walker.step(2, 0);
      if (top) {
        assert.ok(walker.x >= 5, `${name}: stopped at ${walker.x}`);
        assert.ok(Math.abs(walker.feetY - top[1]) < 0.06, `${name}: feet ${walker.feetY} expected ${top[1]}`);
        for (let i = 0; i < 600 && walker.x > -1; i++) walker.step(-2, 0);
        assert.ok(walker.x < -0.8 && Math.abs(walker.feetY) < 0.06, `${name}: descent failed`);
      } else {
        assert.ok(walker.x < 1.7, `${name}: passed blocker at ${walker.x}`);
        assert.ok(walker.feetY < 0.06, `${name}: stepped onto blocker`);
      }
    } finally { world.free(); }
  });
}

test('tread helpers are ray-query-only and share the generated visual treads', () => {
  const scene = scenes['stairs-25'];
  const world = worldFor(scene);
  try {
    const ray = new RAPIER.Ray({ x: 1.9, y: 10, z: 0 }, { x: 0, y: -1, z: 0 });
    const hit = world.castRay(ray, 20, true, undefined, groups(GROUP.CHARACTER, GROUP.STATIC_QUERY));
    assert.ok(hit, 'foot IK must hit helper');
    const helper = scene.meshes.find(m => m.material === MAT.stair_tread_helper)!;
    const expected = Math.min(...helper.positions.filter(p => p[0] >= 1.9).map(p => p[1]));
    assert.ok(Math.abs((10 - hit.timeOfImpact) - expected) < 0.001);
  } finally { world.free(); }
});

test('quay railing stops a 1500 kg car proxy at 10 m/s', () => {
  const world = worldFor(scenes.quay);
  try {
    const body = world.createRigidBody(RAPIER.RigidBodyDesc.dynamic().setTranslation(-3, 0.75, 0)
      .setLinvel(10, 0, 0).setCcdEnabled(true));
    world.createCollider(RAPIER.ColliderDesc.cuboid(1, 0.5, 0.8).setMass(1500)
      .setCollisionGroups(groups(GROUP.VEHICLE, GROUP.STATIC_SOLID)), body);
    let furthest = -3;
    for (let i = 0; i < 120; i++) { world.step(); furthest = Math.max(furthest, body.translation().x); }
    assert.ok(furthest < 1.1, `car passed quay: centre x=${furthest}`);
    assert.ok(Math.abs(body.linvel().x) < 0.5, `car did not stop: vx=${body.linvel().x}`);
  } finally { world.free(); }
});
