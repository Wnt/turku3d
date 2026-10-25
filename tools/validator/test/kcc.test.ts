// Records the Rapier 0.21 KCC step behaviour the validator relies on (box step on a trimesh plane).
// If a Rapier upgrade changes these numbers, the curb thresholds in the validator need a look.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import RAPIER from '@dimforge/rapier3d-compat';
import { DEFAULT_KCC, Walker } from '../src/kcc.ts';
import type { KccConfig } from '../src/kcc.ts';

await RAPIER.init();

function climbs(h: number, speed: number, cfg: Partial<KccConfig> = {}): boolean {
  const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const P = new Float32Array([-10, 0, -10, 30, 0, -10, 30, 0, 10, -10, 0, 10]);
  world.createCollider(RAPIER.ColliderDesc.trimesh(P, new Uint32Array([0, 2, 1, 0, 3, 2])).setCollisionGroups(0x0001ffff));
  world.createCollider(RAPIER.ColliderDesc.cuboid(4, h / 2, 5).setTranslation(6, h / 2, 0).setCollisionGroups(0x0001ffff));
  world.step();
  const w = new Walker(RAPIER, world, { ...DEFAULT_KCC, ...cfg });
  w.place(0.8, 0, 0);
  for (let k = 0; k < 180 && w.x < 5; k++) w.step(speed, 0);
  const ok = w.x > 3 && Math.abs(w.feetY - h) < 0.06;
  world.free();
  return ok;
}

test('probe mode (default): climbs <= 0.27 m, blocked >= 0.30 m at 1.5 and 4 m/s', () => {
  for (const v of [1.5, 4]) {
    for (const h of [0.07, 0.12, 0.17, 0.22, 0.25, 0.27]) assert.ok(climbs(h, v), `h=${h} v=${v} should climb`);
    for (const h of [0.3, 0.35, 0.4]) assert.ok(!climbs(h, v), `h=${h} v=${v} should block`);
  }
});

test('split mode (gravity as a separate move): 0.25 m blocks at 1.5 m/s', () => {
  assert.ok(climbs(0.22, 1.5, { mode: 'split' }));
  assert.ok(!climbs(0.25, 1.5, { mode: 'split' }));
  assert.ok(!climbs(0.4, 1.5, { mode: 'split' }));
});

test('slow walk (1.0 m/s) does not autostep a 0.22 m step in either mode', () => {
  assert.ok(climbs(0.17, 1.0));
  assert.ok(!climbs(0.22, 1.0));
  assert.ok(!climbs(0.22, 1.0, { mode: 'split' }));
});
