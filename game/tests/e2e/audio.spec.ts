import { expect, test, installPad } from './fixtures';

test('audio unlocks, decodes the CC0 bank and builds spatial graph without errors', { tag: ['@audio', '@input'] }, async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await installPad(page);
  await page.goto(`/?backend=${info.project.name}`);
  await page.waitForFunction(() => (window as any).__game?.ready);
  expect(await page.evaluate(() => (window as any).__game.audio.snapshot().state)).toBe('locked');
  await page.getByRole('option', { name: 'New game' }).click();
  await page.getByRole('button', { name: 'Skip' }).click();
  await page.keyboard.press('KeyK');
  await expect.poll(() => page.evaluate(() => (window as any).__game.audio.snapshot()), { timeout: 30_000 })
    .toMatchObject({ state: 'running', loaded: 19, loops: 9, errors: [] });
  await expect.poll(() => page.evaluate(() => (window as any).__game.audio.snapshot().muted)).toBe(true);
  await page.keyboard.press('KeyK');
  await expect.poll(() => page.evaluate(() => (window as any).__game.audio.snapshot().muted)).toBe(false);
  await page.keyboard.down('KeyW');
  await page.waitForFunction(() => Object.keys((window as any).__game.audio.snapshot().played).some(k => k.startsWith('step-')));
  await page.keyboard.up('KeyW');
  await page.evaluate(() => {
    const audio = (window as any).__game.audio, position = { x: 168, y: 3, z: 246 };
    audio.emit({ type: 'curb', position, impulse: 2000 });
    audio.emit({ type: 'impact', position, impulse: 4000 });
    audio.emit({ type: 'door', position, open: true });
    audio.emit({ type: 'door', position, open: false });
    audio.emit({ type: 'gamepad-connected' });
  });
  const snapshot = await page.evaluate(() => (window as any).__game.audio.snapshot());
  for (const sound of ['curb', 'impact', 'door-open', 'door-close', 'connect']) expect(snapshot.played[sound]).toBeGreaterThan(0);
  expect(snapshot.errors).toEqual([]); expect(errors).toEqual([]);
  await page.locator('summary', { hasText: 'Audio' }).click();
  await expect(page.getByRole('slider', { name: 'master volume' })).toBeVisible();
});
