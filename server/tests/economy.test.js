import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import * as economy from '../economy.js';

const source = readFileSync(new URL('../../game/src/gameplay/economy.ts', import.meta.url), 'utf8');
const game = await import(`data:text/javascript,${encodeURIComponent(stripTypeScriptTypes(source))}`);

test('economy constants and timing match the actual game module', () => {
  for (const key of ['BASE_PAY', 'MAX_TIP', 'DOUBLE_BONUS', 'PAR_HANDLING_SECONDS']) assert.equal(economy[key], game[key]);
  for (const distance of [0, 1, 120, 1000, 100_000]) for (const vehicle of ['none', 'scooter', 'car', 'truck']) for (const job of ['short', 'long']) {
    const par = game.parSeconds(distance, vehicle, job);
    assert.equal(economy.parSeconds(distance, vehicle, job), par);
    assert.equal(economy.deliveryDeadline(par), game.deliveryDeadline(par));
    for (let fraction = 0; fraction < 2.1; fraction += 0.01) {
      assert.equal(economy.tip(par * fraction, par), game.tip(par * fraction, par));
    }
  }
});

test('pay envelope matches mission hand-over including the second-stop bonus', () => {
  for (const distance of [100, 745.7, 2500, 4580.3, 7000]) {
    for (const job of ['short', 'long']) for (let t = 0; t <= game.MAX_TIP; t++) {
      const payout = game.basePay(distance, job);
      assert.equal(economy.basePay(distance, job), payout);
      assert.ok(economy.validDelivery({ payout, tip: t, routeMeters: distance, job }));
      assert.equal(economy.validDelivery({ payout: payout + 1, tip: t, routeMeters: distance, job }), false);
    }
  }
  assert.ok(economy.validDelivery({ payout: 10, tip: 1, routeMeters: 100 }));
  assert.equal(economy.validDelivery({ payout: 60, tip: 1, routeMeters: 3000, job: 'unknown' }), false);
  assert.equal(economy.validDelivery({ payout: game.basePay(7001, 'long'), tip: 1, routeMeters: 7001, job: 'long' }), false);
  for (const bad of [{ payout: 7, tip: 0 }, { payout: 10, tip: 0 }, { payout: 6, tip: 9 },
    { payout: 100, tip: 1 }, { payout: 6, tip: -1 }, { payout: 6.5, tip: 1 }]) {
    assert.equal(economy.validDelivery({ routeMeters: 100, ...bad }), false);
  }
});
