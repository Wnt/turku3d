import test from 'node:test';
import assert from 'node:assert/strict';
import { RAPIER as R } from '../src/rapier.ts';
import { crossing } from '../src/bridge-crossing.ts';

test('production car crosses a continuous bridge join both ways; a two metre bank drop fails', async () => {
  await R.init();
  for (const gap of [false, true]) {
    const world = new R.World({ x: 0, y: -9.81, z: 0 });
    try {
      // Separate terrain/deck colliders also exercise an actual seam at x=40.
      for (const [lo, hi, y] of [[-25, 0, 5], [0, 40, 5], [40, 65, gap ? 3 : 5]]) {
        world.createCollider(R.ColliderDesc.cuboid((hi - lo) / 2, 0.5, 6)
          .setTranslation((hi + lo) / 2, y - 0.5, 0).setCollisionGroups(0x0001ffff));
      }
      for (const reverse of [false, true]) {
        const result = crossing(world, reverse ? [40, 0] : [0, 0], reverse ? [0, 0] : [40, 0]);
        if (!gap) assert.equal(result.passed, true, JSON.stringify(result.samples));
        if (gap && reverse) assert.equal(result.passed, false, 'car must not climb the exposed deck end');
      }
    } finally { world.free(); }
  }
});
