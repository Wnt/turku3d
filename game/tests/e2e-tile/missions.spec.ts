import { cachedTest as test, expect, type Page } from '../e2e/fixtures';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot, tileDist } from '../../../tools/dev/environment.mjs';
import { BASE_PAY, MAX_TIP, PERMIT_DELIVERIES, PERMIT_PRICE, START_MONEY } from '../../src/gameplay/economy';
import { lineText } from '../../src/gameplay/content/dialogue';
import { DELIVERIES } from '../../src/gameplay/content/deliveries';
import { MARKET, PIZZERIA } from '../../src/gameplay/content/locations';
import { SAVES_KEY } from '../../src/core/save';
import type { WorldPoint } from '../../src/gameplay/contract';
import { MAX_STREET_STEP_M } from '../../src/gameplay/missions/dropoffs';
import { open, untilSim } from '../e2e/waits';

const shots = path.join(reportsRoot, 'gameplay-missions');
const manifest = JSON.parse(fs.readFileSync(path.join(tileDist, 'manifest.json'), 'utf8'));

interface Missions { phase: string; money: number; permit: number; elapsed: number; shop: { id: string } | null;
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
/** Turns the camera to look past the player at a point. */
const face = (page: Page, point: WorldPoint) => page.evaluate(({ x, z }) => {
  const game = window.__game as any;
  const feet = (game.state as () => { feet: number[] })().feet;
  game.orbit.yaw = Math.atan2(-(x - feet[0]), -(z - feet[2]));
  game.orbit.pitch = -0.35;
}, point);
const showsMessage = (page: Page, text: string) => untilSim(page, t =>
  (window.__game!.state as () => { missions: Missions })().missions.hud.message === t, text, 20);

test('permit, pickup and a timed delivery pay on the published tiles', { tag: ['@gameplay', '@walk'] }, async ({ page }, info) => {
  test.skip(info.project.name !== 'webgpu', 'mission logic is backend-independent; one backend keeps the check fast');
  test.skip(!manifest.visual.t0_0_0 || manifest.visual.t0_0_0.tris < 1000, 'Mission targets need the assembled office layers');
  test.setTimeout(180_000);
  fs.mkdirSync(shots, { recursive: true });
  const shot = async (name: string) => fs.writeFileSync(path.join(shots, `${name}.png`), await page.screenshot());

  const { scale } = await open(page, `/?backend=${info.project.name}&tiles=/tiles/tileset.json`);
  await page.evaluate(key => localStorage.removeItem(key), SAVES_KEY);
  await page.reload();
  await expect(page.locator('.start-intro')).toContainText(lineText('intro_1'));
  // Loading precedes the game clock, so these bounds stay wall-clock, scaled by the measured frame time.
  await expect(page.getByRole('option', { name: 'New game' })).toBeEnabled({ timeout: 60_000 * scale });
  await shot('01-start-intro');
  await page.getByRole('option', { name: 'New game' }).click();
  await page.waitForFunction(() => {
    const s = (window.__game!.state as () => { collision: { tiles: number }; tilesSettled: boolean })();
    return s.collision.tiles >= 9 && s.tilesSettled;
  });
  const spawnGround = await page.evaluate(() => (window.__game!.state as () => { feet: number[] })().feet[1]);
  expect(await missions(page)).toMatchObject({ phase: 'permit_needed', money: START_MONEY,
    hud: { activeTargetId: MARKET.id, message: lineText('hint_permit') } });

  // Black market on the lower quay: the ray starts under the Auransilta deck. Its menu opens on the permit.
  await teleportNear(page, MARKET.point, 4);
  await showsMessage(page, lineText('prompt_enter_market_key'));
  await shot('02-market-prompt');
  await page.keyboard.press('KeyE');
  await expect(page.locator('#shop-menu .shop-option.selected')).toContainText(lineText('option_rent_permit'));
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => (window.__game!.state as () => { missions: Missions })().missions.permit > 0);
  expect(await missions(page)).toMatchObject({ phase: 'at_pizzeria', money: START_MONEY - PERMIT_PRICE, permit: PERMIT_DELIVERIES,
    hud: { message: lineText('market_sold'), activeTargetId: PIZZERIA.id } });
  await shot('03-permit-rented');
  await page.keyboard.press('Escape');
  await expect(page.locator('#shop-menu')).toBeHidden();

  await teleportNear(page, PIZZERIA.point);
  await showsMessage(page, lineText('prompt_enter_pizzeria_key'));
  await shot('04-pizzeria-prompt');
  await page.keyboard.press('KeyE');
  await expect(page.locator('#shop-menu .shop-option.selected')).toContainText(lineText('option_take_order'));
  await page.keyboard.press('Enter');
  const delivery = DELIVERIES[0];
  await page.waitForFunction(() => (window.__game!.state as () => { missions: Missions })().missions.phase === 'carrying');
  const started = await missions(page);
  expect(started.hud).toMatchObject({ activeTargetId: delivery.id, message: lineText(delivery.handover) });
  expect(started.hud.timerSeconds).toBeGreaterThan(0);
  await shot('05-carrying');

  // The drop-off (the pool's sidewalk point for the address, or the scripted fallback) and its marker are at street
  // level: the ground check moves a drop-off off a roof before the marker is placed (missions/dropoffs.ts).
  const stop = await page.evaluate(() => (window.__game!.missions as any).currentStop.point as WorldPoint);
  await teleportNear(page, stop);
  const marker = await page.waitForFunction(id => (window.__game!.state as () => { zones: { id: string; y: number | null }[] })()
    .zones.find(z => z.id === id && z.y !== null)?.y, delivery.id).then(h => h.jsonValue() as Promise<number>);
  const markerStop = await page.evaluate(() => (window.__game!.missions as any).currentStop.point as WorldPoint);
  const feetY = await page.evaluate(() => (window.__game!.state as () => { feet: number[] })().feet[1]);
  console.log(`drop-off ${delivery.address}: ${markerStop.x}, ${markerStop.z}, marker y ${marker.toFixed(2)}, `
    + `feet y ${feetY.toFixed(2)}, spawn ground y ${spawnGround.toFixed(2)}`);
  expect(markerStop).toEqual(stop); // not moved: the published drop-off passed the ground check
  expect(Math.abs(marker - spawnGround)).toBeLessThan(MAX_STREET_STEP_M);
  expect(Math.abs(feetY - marker)).toBeLessThan(MAX_STREET_STEP_M);
  await face(page, stop);
  await showsMessage(page, lineText('prompt_hand_over_key'));
  const before = await missions(page);
  expect(before.hud.timerSeconds!).toBeLessThan(started.hud.timerSeconds!);
  expect(before.elapsed).toBeGreaterThan(0);
  await shot('06-dropoff-prompt');
  await page.keyboard.press('KeyE');
  await page.waitForFunction(() => (window.__game!.state as () => { missions: Missions })().missions.phase === 'delivered');
  const paid = START_MONEY - PERMIT_PRICE + BASE_PAY + MAX_TIP; // well inside 70 % of the par time
  expect(await missions(page)).toMatchObject({ money: paid, hud: { timerSeconds: null, message: lineText(delivery.customer.fast) } });
  await shot('07-customer');
  await showsMessage(page, lineText(`paid_tip_${MAX_TIP}`));
  await shot('08-paid');

  // A reload autosaves on page hide; Continue resumes that save without the intro.
  await page.reload();
  const resume = page.getByRole('option', { name: 'Continue' });
  await expect(resume).toBeEnabled({ timeout: 60_000 * scale });
  await expect(resume).toContainText(delivery.address);
  await expect(page.locator('.start-intro')).toHaveCount(0);
  await resume.click();
  await page.waitForFunction(() => !(window.__game!.state as () => { loading: boolean; startScreen: boolean })().loading);
  expect(await missions(page)).toMatchObject({ permit: PERMIT_DELIVERIES - 1, money: paid, hud: { activeTargetId: PIZZERIA.id } });
  await page.evaluate(key => localStorage.removeItem(key), SAVES_KEY);
});
