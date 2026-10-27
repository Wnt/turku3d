import { expect, test } from '@playwright/test';
test('lab starts and deadline changes output spectrum and level', async ({ page }) => {
  await page.goto('/'); await page.getByRole('button', { name: 'Start audio' }).click();
  await page.getByRole('button', { name: 'Mission' }).click();
  await page.waitForFunction(() => window.musicMetrics?.state === 'mission');
  await page.waitForTimeout(4800); // finish the two-bar state crossfade before measuring the deadline
  const read = async () => page.evaluate(async () => {
    const samples = []; for (let i = 0; i < 40; i++) { await new Promise(r => setTimeout(r, 50)); samples.push(window.musicMetrics); }
    return { level: samples.reduce((sum, item) => sum + item.level, 0) / samples.length,
      centroid: samples.reduce((sum, item) => sum + item.centroid, 0) / samples.length, tempo: samples.at(-1)!.tempo };
  });
  const early = await read();
  await page.locator('#deadline').fill('100');
  const late = await read();
  expect(late.tempo).toBeGreaterThan(early.tempo);
  expect(late.level).toBeGreaterThan(early.level);
  expect(late.centroid).toBeGreaterThan(early.centroid);
});
