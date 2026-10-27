import { test, expect, installPad, padButton, gameState } from './fixtures';
import { OFFICE_EN } from '../../src/core/origin';

test('start screen discovers, disconnects and reconnects an Xbox controller', { tag: ['@input', '@launch', '@synthetic'] }, async ({ page }, info) => {
  await installPad(page);
  await page.goto(`/?backend=${info.project.name}`);
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
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await padButton(page, 0, 0);
  await page.waitForFunction(() => (window.__game!.state as () => any)().grounded);
  const state = await gameState(page);
  expect(state.E).toBeCloseTo(OFFICE_EN.E, 1); expect(state.N).toBeCloseTo(OFFICE_EN.N, 1);
  expect(Math.hypot(state.vehicle!.pos[0] - state.feet[0], state.vehicle!.pos[2] - state.feet[2])).toBeCloseTo(6, 0);
});

