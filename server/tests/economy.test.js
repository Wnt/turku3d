import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import * as economy from '../economy.js';

const source = readFileSync(new URL('../../game/src/gameplay/economy.ts', import.meta.url), 'utf8');
const game = await import(`data:text/javascript,${encodeURIComponent(stripTypeScriptTypes(source))}`);

test('economy constants and timing match the actual game module', () => {
  for (const key of ['BASE_PAY', 'MAX_TIP', 'DOUBLE_BONUS', 'PAR_HANDLING_SECONDS']) assert.equal(economy[key], game[key]);
  for (const distance of [0, 1, 120, 1000, 100_000]) for (const vehicle of ['none', 'scooter', 'car', 'truck']) {
    const par = game.parSeconds(distance, vehicle);
    assert.equal(economy.parSeconds(distance, vehicle), par);
    assert.equal(economy.deliveryDeadline(par), game.deliveryDeadline(par));
    for (let fraction = 0; fraction < 2.1; fraction += 0.01) {
      assert.equal(economy.tip(par * fraction, par), game.tip(par * fraction, par));
    }
  }
});

test('pay envelope matches mission hand-over including the second-stop bonus', () => {
  const missions = readFileSync(new URL('../../game/src/gameplay/missions/missions.ts', import.meta.url), 'utf8');
  const match = missions.match(/const bonus = ([^;]+);\s*this\.s\.money \+= ([^;]+);/);
  assert.ok(match, 'mission payout rule changed: review the server contract');
  const calculate = new Function('stops', 'last', 't', 'BASE_PAY', 'DOUBLE_BONUS',
    `const bonus = ${match[1]}; return ${match[2]};`);
  for (const distance of [0, 100, 2000]) for (const stops of [[{}], [{}, {}]]) {
    for (const last of [false, true]) for (let t = 0; t <= game.MAX_TIP; t++) {
      const total = calculate(stops, last, t, game.BASE_PAY, game.DOUBLE_BONUS);
      assert.ok(economy.validDelivery({ payout: total - t, tip: t, routeMeters: distance }));
    }
  }
  for (const bad of [{ payout: 7, tip: 0 }, { payout: 10, tip: 0 }, { payout: 6, tip: 9 },
    { payout: 100, tip: 1 }, { payout: 6, tip: -1 }, { payout: 6.5, tip: 1 }]) {
    assert.equal(economy.validDelivery({ routeMeters: 100, ...bad }), false);
  }
});
