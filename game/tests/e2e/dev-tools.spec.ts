// Developer tools on the local game (ui/devMenu.ts): the pause menu's Developer page adds money (marked devMoney in
// the save), turns fly mode on, the player flies 20 m up with jump held and a double tap of jump lands them; a
// teleport to the pizzeria uses the save-load placement. Screenshots in $TURKU3D_WORK/reports/gameplay-dev-tools/.
import { isolatedTest as test, expect } from './fixtures';
import { open, untilSim } from './waits';
import fs from 'node:fs';
import path from 'node:path';
import type { Page } from '@playwright/test';
import { reportsRoot } from '../../../tools/dev/environment.mjs';
import { SAVES_KEY } from '../../src/core/save';
import { START_MONEY } from '../../src/gameplay/economy';
import { PIZZERIA } from '../../src/gameplay/content/locations';

const shots = path.join(reportsRoot, 'gameplay-dev-tools');

interface DevState {
  feet: number[]; H: number; grounded: boolean; loading: boolean; startScreen: boolean; fly: boolean | null;
  pauseMenu: { open: boolean; page: string | null };
  missions: { money: number; devMoney: number };
  recovery: { armed: boolean; lost: unknown };
}
const devState = (page: Page) => page.evaluate(() => (window.__game!.state as () => DevState)());
const panel = (page: Page) => page.locator('#pause-menu .menu-panel');
const option = (page: Page, name: string) => page.locator('#pause-menu .menu-option', { hasText: name });

test('Developer page: money, fly 20 m up and land, teleport', { tag: ['@gameplay', '@synthetic'] }, async ({ page }, info) => {
  const backend = info.project.name;
  fs.mkdirSync(shots, { recursive: true });
  const shot = async (name: string) => fs.writeFileSync(path.join(shots, `${backend}-${name}.png`), await page.screenshot());

  await open(page, `/?backend=${backend}`);
  await page.getByRole('option', { name: 'New game' }).click();
  await untilSim(page, () => {
    const s = (window.__game!.state as () => DevState)();
    return !s.loading && !s.startScreen && s.grounded && s.recovery.armed;
  }, null, 20);
  expect((await devState(page)).fly).toBe(false); // localhost: the tools run, flight starts off

  // Escape opens the pause menu; Developer sits before Quit.
  await page.keyboard.press('Escape');
  await expect(panel(page)).toHaveAttribute('data-page', 'pause');
  await option(page, 'Developer').click();
  await expect(panel(page)).toHaveAttribute('data-page', 'dev');
  await option(page, 'Money +10 000 €').click();
  await expect(page.locator('#pause-menu .menu-text')).toHaveText(`Money ${(START_MONEY + 10_000).toLocaleString('en-US').replace(/,/g, ' ')} €.`);
  expect((await devState(page)).missions).toMatchObject({ money: START_MONEY + 10_000, devMoney: 10_000 });
  await option(page, 'Fly mode on').click();
  await expect(page.locator('#pause-menu .menu-text')).toHaveText('Fly mode on.');
  await expect(option(page, 'Fly mode off')).toBeVisible();
  await shot('01-developer-page');
  await page.keyboard.press('Escape'); // back to the pause root
  await expect(panel(page)).toHaveAttribute('data-page', 'pause');
  await page.keyboard.press('Escape'); // resume
  await expect(page.locator('#pause-menu')).toBeHidden();

  // Jump held rises at three times walking speed with no gravity and nobody lost.
  const ground = await devState(page);
  expect(ground.fly).toBe(true);
  await page.keyboard.down('Space');
  await untilSim(page, h => (window.__game!.state as () => DevState)().H > h, ground.H + 20, 15);
  await page.keyboard.up('Space');
  const high = await devState(page);
  expect(high.grounded).toBe(false);
  const t0 = await page.evaluate(() => window.__game!.clock as { simSeconds: number });
  await untilSim(page, t => (window.__game!.clock as { simSeconds: number }).simSeconds > t, t0.simSeconds + 1, 3); // hover 1 s
  const hover = await devState(page);
  expect(Math.abs(hover.H - high.H)).toBeLessThan(0.5);
  expect(hover.recovery.lost).toBeNull();
  await shot('02-flying-20m');

  // A double tap of jump ends the flight: the player falls back and lands; safe reset re-arms on the ground.
  // Two Space taps two frames apart, timed in the page so a slow round trip cannot stretch the double tap.
  await page.evaluate(() => new Promise<void>(resolve => {
    const tap = () => { for (const type of ['keydown', 'keyup']) dispatchEvent(new KeyboardEvent(type, { code: 'Space' })); };
    let n = 0;
    const frame = () => { if (n === 0 || n === 2) tap(); if (++n > 2) resolve(); else requestAnimationFrame(frame); };
    requestAnimationFrame(frame);
  }));
  await untilSim(page, () => (window.__game!.state as () => DevState)().fly === false, null, 2);
  await untilSim(page, h => {
    const s = (window.__game!.state as () => DevState)();
    return s.grounded && Math.abs(s.H - h) < 0.3 && s.recovery.armed;
  }, ground.H, 15);
  const landed = await devState(page);
  expect(Math.hypot(landed.feet[0] - ground.feet[0], landed.feet[2] - ground.feet[2])).toBeLessThan(1);
  expect(landed.recovery.lost).toBeNull();
  await shot('03-landed');

  // Teleport to the pizzeria: the save-load placement puts the player on its ground.
  await page.keyboard.press('Escape');
  await option(page, 'Developer').click();
  await option(page, 'Teleport to...').click();
  await expect(panel(page)).toHaveAttribute('data-page', 'dev-teleport');
  await shot('04-teleport-list');
  await option(page, 'Pizzeria').click();
  await untilSim(page, p => {
    const s = (window.__game!.state as () => DevState)();
    return !s.loading && !s.pauseMenu.open && s.grounded && Math.hypot(s.feet[0] - p.x, s.feet[2] - p.z) < 1;
  }, PIZZERIA.point, 20);
  await shot('05-teleported-pizzeria');

  // The save marks the developer money, so no scoreboard counts it.
  await page.keyboard.press('Escape');
  await option(page, 'Save game').click();
  await option(page, 'Slot 1').click();
  await expect(page.locator('#pause-menu .menu-text')).toHaveText('Saved to Slot 1.');
  const stored = await page.evaluate(key => JSON.parse(localStorage.getItem(key)!), SAVES_KEY);
  expect(stored.slots.slot1.stats).toMatchObject({ money: START_MONEY + 10_000, devMoney: 10_000 });
  expect(JSON.parse(stored.slots.slot1.mission)).toMatchObject({ devMoney: 10_000 });
  expect((await devState(page)).fly).toBe(false);
});
