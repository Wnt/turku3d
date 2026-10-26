import { cachedTest as test, expect, type Page } from '../e2e/fixtures';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot } from '../../../tools/dev/environment.mjs';
import { BASE_PAY, LICENCE_PRICE, MAX_TIP, START_MONEY } from '../../src/gameplay/economy';
import { lineText } from '../../src/gameplay/content/dialogue';
import { DELIVERIES } from '../../src/gameplay/content/deliveries';
import { MARKET, PIZZERIA } from '../../src/gameplay/content/locations';
import { SAVE_KEY } from '../../src/gameplay/missions/missions';
import type { WorldPoint } from '../../src/gameplay/contract';

const shots = path.join(reportsRoot, 'gameplay-missions');

interface Missions { phase: string; money: number; licence: boolean; elapsed: number;
  hud: { message: string | null; timerSeconds: number | null; activeTargetId: string | null; money: number } }
const missions = (page: Page) => page.evaluate(() => (window.__game!.state as () => { missions: Missions })().missions);

/** Puts the player on the ground 1.5 m from a target. `below` starts the ground ray under a bridge deck. */
async function teleportNear(page: Page, point: WorldPoint, below = 40) {
  await page.evaluate(async ({ x, z, below }) => {
    const game = window.__game as any;
    const id = `c_${Math.floor(x / 62.5)}_${Math.floor(z / 62.5)}`;
    for (let i = 0; i < 600 && !game.physics.loaded.has(id); i++) await new Promise(r => setTimeout(r, 50));
    const hit = await game.physics.raycast({ x, y: below, z }, { x: 0, y: -1, z: 0 }, below + 20);
    if (!hit) throw new Error(`no ground at ${x}, ${z}`);
    game.physics.setCharacterEnabled(true, { x, y: hit.point.y + 0.1, z });
  }, { x: point.x + 1.5, z: point.z, below });
  await page.waitForFunction(({ x, z }) => {
    const s = (window.__game!.state as () => { feet: number[]; grounded: boolean })();
    return Math.hypot(s.feet[0] - x, s.feet[2] - z) < 1 && s.grounded;
  }, { x: point.x + 1.5, z: point.z });
}
const showsMessage = (page: Page, text: string) => page.waitForFunction(t =>
  (window.__game!.state as () => { missions: Missions })().missions.hud.message === t, text, { timeout: 20_000 });

test('licence, pickup and a timed delivery pay on the published tiles', { tag: ['@gameplay', '@walk'] }, async ({ page }, info) => {
  test.skip(info.project.name !== 'webgpu', 'mission logic is backend-independent; one backend keeps the check fast');
  test.setTimeout(180_000);
  fs.mkdirSync(shots, { recursive: true });
  const shot = async (name: string) => fs.writeFileSync(path.join(shots, `${name}.png`), await page.screenshot());

  await page.goto(`/?backend=${info.project.name}&tiles=/tiles/tileset.json`);
  await page.evaluate(key => localStorage.removeItem(key), SAVE_KEY);
  await page.reload();
  await expect(page.locator('.start-intro')).toContainText(lineText('intro_1'));
  await expect(page.getByRole('button', { name: 'Start playing' })).toBeEnabled({ timeout: 60_000 });
  await shot('01-start-intro');
  await page.getByRole('button', { name: 'Start playing' }).click();
  await page.waitForFunction(() => {
    const s = (window.__game!.state as () => { collision: { tiles: number }; tilesSettled: boolean })();
    return s.collision.tiles >= 9 && s.tilesSettled;
  });
  expect(await missions(page)).toMatchObject({ phase: 'licence_needed', money: START_MONEY,
    hud: { activeTargetId: MARKET.id, message: lineText('hint_licence') } });

  // Black market on the lower quay: the ray starts under the Auransilta deck.
  await teleportNear(page, MARKET.point, 4);
  await showsMessage(page, lineText('prompt_buy_licence_key'));
  await shot('02-market-prompt');
  await page.keyboard.press('KeyE');
  await page.waitForFunction(() => (window.__game!.state as () => { missions: Missions })().missions.licence);
  expect(await missions(page)).toMatchObject({ phase: 'at_pizzeria', money: START_MONEY - LICENCE_PRICE,
    hud: { message: lineText('market_sold'), activeTargetId: PIZZERIA.id } });
  await shot('03-licence-bought');

  await teleportNear(page, PIZZERIA.point);
  await showsMessage(page, lineText('prompt_take_pizza_key'));
  await shot('04-pizzeria-prompt');
  await page.keyboard.press('KeyE');
  const delivery = DELIVERIES[0];
  await page.waitForFunction(() => (window.__game!.state as () => { missions: Missions })().missions.phase === 'carrying');
  const started = await missions(page);
  expect(started.hud).toMatchObject({ activeTargetId: delivery.id, message: lineText(delivery.handover) });
  expect(started.hud.timerSeconds).toBeGreaterThan(0);
  await shot('05-carrying');

  await teleportNear(page, delivery.point);
  await showsMessage(page, lineText('prompt_hand_over_key'));
  const before = await missions(page);
  expect(before.hud.timerSeconds!).toBeLessThan(started.hud.timerSeconds!);
  expect(before.elapsed).toBeGreaterThan(0);
  await shot('06-dropoff-prompt');
  await page.keyboard.press('KeyE');
  await page.waitForFunction(() => (window.__game!.state as () => { missions: Missions })().missions.phase === 'delivered');
  const paid = START_MONEY - LICENCE_PRICE + BASE_PAY + MAX_TIP; // well inside 70 % of the par time
  expect(await missions(page)).toMatchObject({ money: paid, hud: { timerSeconds: null, message: lineText(delivery.customer.fast) } });
  await shot('07-customer');
  await showsMessage(page, lineText(`paid_tip_${MAX_TIP}`));
  await shot('08-paid');

  // A reload continues the saved game without the intro.
  await page.reload();
  await expect(page.getByRole('button', { name: 'Start playing' })).toBeEnabled({ timeout: 60_000 });
  await expect(page.locator('.start-intro')).toHaveCount(0);
  expect(await missions(page)).toMatchObject({ licence: true, money: paid, hud: { activeTargetId: PIZZERIA.id } });
  await page.evaluate(key => localStorage.removeItem(key), SAVE_KEY);
});
