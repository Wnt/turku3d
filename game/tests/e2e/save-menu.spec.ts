import { isolatedTest as test, expect, gameState } from './fixtures';
import { open, untilSim, frames } from './waits';
import fs from 'node:fs';
import path from 'node:path';
import type { Page } from '@playwright/test';
import { reportsRoot } from '../../../tools/dev/environment.mjs';
import { OFFICE_EN } from '../../src/core/origin';
import { SAVES_KEY } from '../../src/core/save';
import { KIDNEY_PAYOUT, PERMIT_DELIVERIES, PERMIT_PRICE, START_MONEY } from '../../src/gameplay/economy';
import { MARKET } from '../../src/gameplay/content/locations';

const shots = path.join(reportsRoot, 'gameplay-save-menu');

interface MenuState {
  E: number; N: number; grounded: boolean; loading: boolean; startScreen: boolean; autosave: string | null;
  pauseMenu: { open: boolean; page: string | null };
  missions: { money: number; permit: number; kidney: boolean; phase: string };
}
const menuState = (page: Page) => page.evaluate(() => (window.__game!.state as () => MenuState)());

/** The black market lies outside the synthetic tiles: its menu choices go straight through the mission system. */
const buyAtMarket = (page: Page, option: string) => page.evaluate(({ point, option }) => {
  const missions = window.__game!.missions as { update(dt: number, frame: object): unknown };
  const frame = (extra: object) => ({ position: point, interact: false, input: 'keyboard', vehicleId: null, ...extra });
  missions.update(0, frame({ interact: true }));
  missions.update(0, frame({ choose: option }));
  missions.update(0, frame({ leave: true }));
}, { point: MARKET.point, option });

/** Waits until a picked game is placed: menus closed, the player standing on the ground. */
const playing = (page: Page) => untilSim(page, () => {
  const s = (window.__game!.state as () => MenuState)();
  return !s.loading && !s.startScreen && !s.pauseMenu.open && s.grounded;
}, null, 20);

async function walk(page: Page, metres: number) {
  const from = await gameState(page);
  await page.keyboard.down('KeyW');
  await untilSim(page, ([x, z, d]) => {
    const s = (window.__game!.state as () => { feet: number[] })();
    return Math.hypot(s.feet[0] - x, s.feet[2] - z) > d;
  }, [from.feet[0], from.feet[2], metres], 20);
  await page.keyboard.up('KeyW');
  await untilSim(page, () => {
    const s = (window.__game!.state as () => { vel: number[]; grounded: boolean })();
    return s.grounded && Math.hypot(s.vel[0], s.vel[2]) < 0.05;
  }, null, 10);
}

/** One key press per frame: presses inside one frame are a single menu step (Input edges are per frame). */
async function tap(page: Page, key: string) {
  await page.keyboard.press(key);
  await frames(page, 2);
}

const selected = (page: Page) => page.locator('#pause-menu .menu-option.selected');

test('save to slot 2, quit, load slot 2 restores money, permit and position; New game starts clean', { tag: ['@gameplay', '@input', '@synthetic'] }, async ({ page }, info) => {
  const backend = info.project.name;
  fs.mkdirSync(shots, { recursive: true });
  const shot = async (name: string) => fs.writeFileSync(path.join(shots, `${backend}-${name}.png`), await page.screenshot());

  await open(page, `/?backend=${backend}`);
  const newGame = page.getByRole('option', { name: 'New game' });
  await expect(newGame).toBeEnabled();
  await expect(page.getByRole('option', { name: 'Load game' })).toHaveAttribute('aria-disabled', 'true');
  await expect(page.getByRole('option', { name: 'Continue' })).toHaveCount(0);
  await newGame.click();
  await expect(page.locator('#intro-dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Skip' }).click();
  await playing(page);
  const start = await menuState(page);

  await buyAtMarket(page, 'rent_permit');
  expect((await menuState(page)).missions).toMatchObject({ money: START_MONEY - PERMIT_PRICE, permit: PERMIT_DELIVERIES });
  await walk(page, 3);
  const saved = await menuState(page);

  // Escape opens the pause menu; the keyboard walks it like a shop menu and W no longer moves the player.
  await tap(page, 'Escape');
  await expect(page.locator('#pause-menu')).toBeVisible();
  await expect(selected(page)).toHaveText('Resume');
  await page.keyboard.down('KeyW');
  await frames(page, 20);
  await page.keyboard.up('KeyW');
  const locked = await menuState(page);
  expect(Math.hypot(locked.E - saved.E, locked.N - saved.N)).toBeLessThan(0.05);
  await expect(selected(page)).toHaveText('Quit to start screen'); // W is menu up: it wrapped from Resume
  await shot('01-pause-menu');
  await tap(page, 'ArrowDown');
  await tap(page, 'ArrowDown');
  await expect(selected(page)).toHaveText('Respawn at the start');
  await tap(page, 'ArrowDown');
  await expect(selected(page)).toHaveText('Scoreboard');
  await tap(page, 'ArrowDown');
  await expect(selected(page)).toHaveText('Save game');
  await tap(page, 'Enter');
  await expect(page.locator('#pause-menu .menu-panel')).toHaveAttribute('data-page', 'save');
  await tap(page, 'ArrowDown');
  await expect(selected(page)).toContainText('Slot 2 · Empty');
  await tap(page, 'Enter');
  await expect(page.locator('#pause-menu .menu-text')).toHaveText('Saved to Slot 2.');
  await shot('02-saved-slot-2');
  const stored = await page.evaluate(key => JSON.parse(localStorage.getItem(key)!), SAVES_KEY);
  expect(stored.slots.slot2).toMatchObject({ version: 1, stats: { deliveries: 0, money: START_MONEY - PERMIT_PRICE },
    player: { E: expect.closeTo(saved.E, 1), N: expect.closeTo(saved.N, 1) } });
  expect(stored.slots.slot2.label).toMatch(/^Before the first delivery · \d\d:\d\d$/);

  // Overwriting asks first; Cancel keeps the slot.
  await expect(page.locator('#pause-menu .menu-panel')).toHaveAttribute('data-page', 'pause');
  await page.getByRole('option', { name: 'Save game' }).click();
  await page.getByRole('option', { name: 'Slot 2 · Before the first delivery' }).click();
  await expect(page.locator('#pause-menu .menu-text')).toContainText('Overwrite Slot 2');
  await shot('03-overwrite-confirm');
  await page.getByRole('option', { name: 'Cancel' }).click();
  await tap(page, 'Escape'); // back to the root
  await tap(page, 'Escape'); // resume
  await expect(page.locator('#pause-menu')).toBeHidden();

  // Play on after saving: more money, another walk. Then quit to the start screen (autosave).
  await buyAtMarket(page, 'sell_kidney');
  await walk(page, 4);
  const later = await menuState(page);
  expect(later.missions.money).toBe(START_MONEY - PERMIT_PRICE + KIDNEY_PAYOUT);
  // Respawn at the start (every build) asks first, then puts the player back at the spawn; money and permit stay.
  await tap(page, 'Escape');
  await page.getByRole('option', { name: 'Respawn at the start' }).click();
  await expect(page.locator('#pause-menu .menu-text')).toHaveText('Respawn? You keep your money and order.');
  await page.getByRole('option', { name: 'Respawn', exact: true }).click();
  await playing(page);
  const respawned = await menuState(page);
  expect(Math.hypot(respawned.E - start.E, respawned.N - start.N)).toBeLessThan(0.5);
  expect(respawned.missions).toMatchObject({ money: later.missions.money, permit: PERMIT_DELIVERIES, phase: later.missions.phase });
  await shot('03b-respawned');
  await tap(page, 'Escape');
  await page.getByRole('option', { name: 'Quit to start screen' }).click();
  await expect(page.locator('#start-screen')).toBeVisible();
  expect((await menuState(page)).autosave).toBe('quit');
  const resume = page.getByRole('option', { name: 'Continue' });
  await expect(resume).toContainText(`0 deliveries · ${later.missions.money} €`);
  await shot('04-start-continue');

  await page.getByRole('option', { name: 'Load game' }).click();
  await expect(page.getByRole('option', { name: 'Autosave · Before the first delivery' })).toBeEnabled();
  await expect(page.getByRole('option', { name: 'Slot 1 · Empty' })).toHaveAttribute('aria-disabled', 'true');
  await expect(page.getByRole('option', { name: 'Slot 3 · Empty' })).toHaveAttribute('aria-disabled', 'true');
  const slot2 = page.getByRole('option', { name: 'Slot 2 · Before the first delivery' });
  await expect(slot2).toContainText(`0 deliveries · ${START_MONEY - PERMIT_PRICE} €`);
  await shot('05-load-list');
  await slot2.click();
  await playing(page);
  const loaded = await menuState(page);
  expect(loaded.missions).toMatchObject({ money: START_MONEY - PERMIT_PRICE, permit: PERMIT_DELIVERIES, kidney: false });
  expect(Math.hypot(loaded.E - saved.E, loaded.N - saved.N)).toBeLessThan(0.5);
  await frames(page, 10);
  await shot('06-loaded-slot-2');

  // New game from the pause menu confirms, then starts clean at the spawn point.
  await tap(page, 'Escape');
  await page.getByRole('option', { name: 'New game' }).click();
  await expect(page.locator('#pause-menu .menu-panel')).toHaveAttribute('data-page', 'confirm-new');
  await page.getByRole('option', { name: 'Start a new game' }).click();
  await expect(page.locator('#intro-dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Skip' }).click();
  await playing(page);
  const fresh = await menuState(page);
  expect(fresh.missions).toMatchObject({ money: START_MONEY, permit: 0, kidney: false, phase: 'permit_needed' });
  expect(Math.hypot(fresh.E - OFFICE_EN.E, fresh.N - OFFICE_EN.N)).toBeLessThan(0.5);
  await shot('07-new-game');
  expect((await gameState(page)).errors).toEqual([]);
});
