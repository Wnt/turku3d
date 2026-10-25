// Native Node 24: no Vite, DOM, three.js, TS compiler or browser loader.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { CURB_SCENARIOS, runCurbScenario, vehicleFixture } from '../fixtures/vehicle.ts';
import { MAT } from '../../src/shared/col/materials.ts';
const requireValidator = createRequire(new URL('../../../tools/validator/package.json', import.meta.url));
const R = requireValidator('@dimforge/rapier3d-compat');
await R.init();

for (const scenario of CURB_SCENARIOS) {
  test(`Node curb ${scenario.height} m / ${scenario.speed} m/s`, () => {
    const r = runCurbScenario(R, scenario);
    assert.ok(r.strikes > 0);
    if (scenario.blocked) {
      assert.ok(r.state.pos.x < -1.5);
      assert.ok(Math.abs(r.state.forwardSpeed) < 0.1);
      assert.equal(r.state.curbStrike?.blocked, true);
    } else {
      assert.ok(r.maxPitch >= 2, `pitch ${r.maxPitch}`);
      assert.ok(r.loss >= 0.05 && r.loss <= 0.2, `loss ${r.loss}`);
      assert.ok(r.state.pos.x > 1.7);
      console.log(`height=${scenario.height} speed=${scenario.speed} pitch=${r.maxPitch.toFixed(2)}deg loss=${(r.loss * 100).toFixed(2)}%`);
    }
  });
}

test('Node CCD: 30 m/s into a 3 cm building shell', () => {
  const { world, vehicle } = vehicleFixture(R, 8, MAT.building, false, 0.03);
  try {
    vehicle.body.setLinvel({ x: 30, y: 0, z: 0 }, true);
    for (let i = 0; i < 120; i++) {
      vehicle.step(1 / 60, { throttle: 0, brake: 0, steer: 0, handbrake: false }); world.step();
      assert.ok(vehicle.state().pos.x < -2);
    }
  } finally { world.free(); }
});
