import { test, expect, installPad, padButton, gameState } from './fixtures';
import { OFFICE_EN } from '../../src/core/origin';

test('start screen discovers, disconnects and reconnects an Xbox controller', { tag: ['@input', '@launch', '@synthetic'] }, async ({ page }, info) => {
  await installPad(page);
  await page.goto(`/?scene=dev&backend=${info.project.name}`);
  await expect(page.getByRole('option', { name: 'New game' })).toBeEnabled();
  await expect(page.locator('#gamepad-status')).toHaveText('Xbox Wireless Controller connected');
  await expect(page.locator('#start-bindings')).toContainText('Ⓨ');
  await expect(page.locator('#start-bindings')).toContainText('RT');
  await page.evaluate(() => { window.__virtualPad.connected = false; });
  await expect(page.locator('#gamepad-status')).toContainText('No controller detected');
  await page.evaluate(() => { window.__virtualPad.connected = true; });
  await expect(page.locator('#gamepad-status')).toHaveText('Xbox Wireless Controller connected');
  await info.attach('start-screen', { body: await page.screenshot(), contentType: 'image/png' });
  await padButton(page, 0, 1);
  await expect(page.locator('#intro-dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Skip' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await padButton(page, 0, 0);
  await page.waitForFunction(() => (window.__game!.state as () => any)().grounded);
  const state = await gameState(page);
  expect(state.E).toBeCloseTo(OFFICE_EN.E, 1); expect(state.N).toBeCloseTo(OFFICE_EN.N, 1);
  expect(Math.hypot(state.vehicle!.pos[0] - state.feet[0], state.vehicle!.pos[2] - state.feet[2])).toBeCloseTo(6, 0);
});


test('Firefox requires acknowledging the unsupported-browser warning before gameplay', { tag: ['@launch', '@input', '@synthetic'] }, async ({ page }, info) => {
  // Exercise the browser-specific UX in the normal Chromium CI project without needing a Firefox installation.
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'userAgent', {
      configurable: true, value: 'Mozilla/5.0 (X11; Linux x86_64; rv:141.0) Gecko/20100101 Firefox/141.0',
    });
  });
  await page.goto(`/?scene=dev&backend=${info.project.name}`);

  const warning = page.locator('#firefox-warning');
  await expect(warning).toBeVisible();
  await expect(warning).toContainText('Firefox is');
  await expect(warning).toContainText('Google Chrome');
  const newGame = page.getByRole('option', { name: 'New game' });
  await expect(newGame).toHaveAttribute('aria-disabled', 'true');

  // Even a programmatic pointer event behind the overlay cannot start the game.
  await newGame.evaluate(element => element.dispatchEvent(new Event('pointerup', { bubbles: true })));
  await expect(page.locator('#intro-dialog')).toBeHidden();
  expect(await page.evaluate(() => (window.__game!.state as () => { startScreen: boolean })().startScreen)).toBe(true);

  await warning.getByRole('button', { name: 'I understand · let me suffer' }).click();
  await expect(warning).toBeHidden();
  await expect(newGame).toHaveAttribute('aria-disabled', 'false');
  await newGame.click();
  await expect(page.locator('#intro-dialog')).toBeVisible();
});
