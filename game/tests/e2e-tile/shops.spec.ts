import { cachedTest as test, expect, type Page } from '../e2e/fixtures';
import { frames } from '../e2e/waits';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot, tileDist } from '../../../tools/dev/environment.mjs';
import { PERMIT_DELIVERIES, PERMIT_PRICE, START_MONEY } from '../../src/gameplay/economy';
import { lineText } from '../../src/gameplay/content/dialogue';
import { DEALER, MARKET, PIZZERIA } from '../../src/gameplay/content/locations';
import { SAVE_KEY } from '../../src/gameplay/missions/missions';
import type { WorldPoint } from '../../src/gameplay/contract';

const shots = path.join(reportsRoot, 'gameplay-shops-ui');
const manifest = JSON.parse(fs.readFileSync(path.join(tileDist, 'manifest.json'), 'utf8'));

interface Zone { id: string; visible: boolean; opacity: number; labelShown: boolean; y: number | null }
interface Snapshot {
  feet: number[]; grounded: boolean; zones: Zone[]; shopMenu: { open: boolean; selected: number };
  missions: { money: number; permit: number; shop: { id: string } | null; hud: { message: string | null } };
}
const state = (page: Page) => page.evaluate(() => (window.__game!.state as () => Snapshot)());

/** A walkable spot `radius` m from `target` with a clear straight line to it at about the same ground height. */
async function approachFrom(page: Page, target: WorldPoint, radius: number, rayTop: number) {
  return page.evaluate(async ({ target, radius, rayTop }) => {
    const game = window.__game as any;
    const down = { x: 0, y: -1, z: 0 };
    const ground = async (x: number, z: number) => (await game.physics.raycast({ x, y: rayTop, z }, down, rayTop + 30))?.point.y ?? null;
    const id = `c_${Math.floor(target.x / 62.5)}_${Math.floor(target.z / 62.5)}`;
    for (let i = 0; i < 600 && !game.physics.loaded.has(id); i++) await new Promise(r => setTimeout(r, 50));
    const y0 = await ground(target.x, target.z);
    if (y0 === null) throw new Error(`no ground at ${target.x}, ${target.z}`);
    for (let k = 0; k < 24; k++) {
      const a = (k / 24) * 2 * Math.PI;
      const x = target.x + radius * Math.cos(a), z = target.z + radius * Math.sin(a);
      const y = await ground(x, z);
      if (y === null || Math.abs(y - y0) > 0.35) continue;
      const dir = { x: target.x - x, y: 0, z: target.z - z };
      const len = Math.hypot(dir.x, dir.z);
      const blocked = await game.physics.raycast({ x, y: y + 0.7, z }, { x: dir.x / len, y: 0, z: dir.z / len }, len);
      if (!blocked) return { x, y, z };
    }
    throw new Error(`no clear approach ${radius} m around ${target.x}, ${target.z}`);
  }, { target, radius, rayTop });
}

/** Puts the player at `at`, turns the camera to look at `target` and waits for the feet to settle there. */
async function placeFacing(page: Page, at: { x: number; y: number; z: number }, target: WorldPoint) {
  await page.evaluate(({ at, target }) => {
    const game = window.__game as any;
    game.physics.setCharacterEnabled(true, { x: at.x, y: at.y + 0.1, z: at.z });
    game.orbit.yaw = Math.atan2(-(target.x - at.x), -(target.z - at.z));
    game.orbit.pitch = -0.18;
  }, { at, target });
  await page.waitForFunction(({ x, z }) => {
    const s = (window.__game!.state as () => { feet: number[]; grounded: boolean })();
    return Math.hypot(s.feet[0] - x, s.feet[2] - z) < 1 && s.grounded;
  }, { x: at.x, z: at.z });
}

async function openShop(page: Page, target: WorldPoint, rayTop: number) {
  await placeFacing(page, await approachFrom(page, target, 1.5, rayTop), target);
  await page.keyboard.press('KeyE');
  await expect(page.locator('#shop-menu')).toBeVisible();
}

test('shop zones glow in the world and the black market menu locks movement while renting a permit', { tag: ['@gameplay', '@walk'] }, async ({ page }, info) => {
  test.skip(!manifest.visual.t0_0_0 || manifest.visual.t0_0_0.tris < 1000, 'Shop zones need the assembled office layers');
  test.setTimeout(240_000);
  fs.mkdirSync(shots, { recursive: true });
  const backend = info.project.name;
  const shot = async (name: string) => fs.writeFileSync(path.join(shots, `${backend}-${name}.png`), await page.screenshot());

  await page.goto(`/?backend=${backend}&tiles=/tiles/tileset.json`);
  await page.evaluate(key => localStorage.removeItem(key), SAVE_KEY);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Start playing' })).toBeEnabled({ timeout: 60_000 });
  await page.getByRole('button', { name: 'Start playing' }).click();
  await page.waitForFunction(() => {
    const s = (window.__game!.state as () => { collision: { tiles: number }; tilesSettled: boolean })();
    return s.collision.tiles >= 9 && s.tilesSettled;
  });

  // The pizzeria's zone seen from the street, about 14 m away.
  await placeFacing(page, await approachFrom(page, PIZZERIA.point, 14, 40), PIZZERIA.point);
  await page.waitForFunction(() => (window.__game!.state as () => Snapshot)().zones.some(z => z.id === 'pizzeria' && z.visible && z.labelShown));
  await expect(page.locator('.zone-label', { hasText: lineText('zone_pizzeria') })).toBeVisible();
  await frames(page, 20);
  await shot('01-zone-from-street');

  // Walk into the black market zone on the quay under Auransilta.
  await placeFacing(page, await approachFrom(page, MARKET.point, 7, 4), MARKET.point);
  await page.waitForFunction(() => (window.__game!.state as () => Snapshot)().zones.some(z => z.id === 'market' && z.visible && z.labelShown));
  await expect(page.locator('.zone-label', { hasText: lineText('zone_market') })).toBeVisible();
  await page.keyboard.down('KeyW');
  await page.waitForFunction(p => {
    const s = (window.__game!.state as () => Snapshot)();
    return Math.hypot(s.feet[0] - p.x, s.feet[2] - p.z) < 2.2;
  }, MARKET.point);
  await page.keyboard.up('KeyW');
  await page.waitForFunction(text => (window.__game!.state as () => Snapshot)().missions.hud.message === text,
    lineText('prompt_enter_market_key'));
  expect((await state(page)).zones.find(z => z.id === 'market')).toMatchObject({ visible: true });
  await shot('02-market-zone-prompt');

  // E opens the menu; W then only moves the selection, never the player.
  await page.keyboard.press('KeyE');
  await expect(page.locator('#shop-menu')).toBeVisible();
  await expect(page.locator('#shop-menu .shop-line')).toHaveText(lineText('market_greet'));
  await expect(page.locator('#shop-menu .shop-option.selected')).toContainText(lineText('option_rent_permit'));
  const before = await state(page);
  await page.keyboard.down('KeyW');
  await frames(page, 45);
  await page.keyboard.up('KeyW');
  const locked = await state(page);
  expect(Math.hypot(locked.feet[0] - before.feet[0], locked.feet[2] - before.feet[2])).toBeLessThan(0.05);
  expect(locked.shopMenu.open).toBe(true);
  await page.keyboard.press('ArrowDown'); // W moved the selection up to Leave; down wraps to the permit
  await expect(page.locator('#shop-menu .shop-option.selected')).toContainText(lineText('option_rent_permit'));
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => (window.__game!.state as () => Snapshot)().missions.permit > 0);
  expect((await state(page)).missions).toMatchObject({ money: START_MONEY - PERMIT_PRICE, permit: PERMIT_DELIVERIES });
  await expect(page.locator('#shop-menu .shop-line')).toHaveText(lineText('market_sold'));
  await expect(page.locator('#shop-menu .shop-status')).toContainText(`${PERMIT_DELIVERIES} deliveries`);
  await expect(page.locator('#shop-menu .shop-option[data-option="rent_permit"]')).toHaveClass(/disabled/);
  await shot('03-market-menu');

  // Escape closes the menu and gives the controls back.
  await page.keyboard.press('Escape');
  await expect(page.locator('#shop-menu')).toBeHidden();
  expect((await state(page)).missions.shop).toBeNull();
  const free = await state(page);
  await page.keyboard.down('KeyS');
  await page.waitForFunction(f => {
    const s = (window.__game!.state as () => Snapshot)();
    return Math.hypot(s.feet[0] - f[0], s.feet[2] - f[2]) > 1;
  }, free.feet);
  await page.keyboard.up('KeyS');

  // The other two menus, as the player sees them with a permit.
  await openShop(page, PIZZERIA.point, 40);
  await expect(page.locator('#shop-menu .shop-option[data-option="take_order"] .shop-detail')).toContainText(' m · par ');
  await shot('04-pizzeria-menu');
  await page.keyboard.press('Escape');
  await expect(page.locator('#shop-menu')).toBeHidden();
  await openShop(page, DEALER.point, 40);
  await expect(page.locator('#shop-menu .shop-line')).toHaveText(lineText('dealer_greet'));
  await shot('05-dealer-menu');
  await page.keyboard.press('Escape');
  await expect(page.locator('#shop-menu')).toBeHidden();
  await page.evaluate(key => localStorage.removeItem(key), SAVE_KEY);
});
