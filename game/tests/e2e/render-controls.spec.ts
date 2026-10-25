import { test, expect } from '@playwright/test';

test('HiDPI SSE follows output pixels after resize and the debug time slider refreshes sunlight', async ({ browser }, info) => {
  const context = await browser.newContext({ viewport: { width: 900, height: 600 }, deviceScaleFactor: 2 });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  try {
    await page.goto(`/?backend=${info.project.name}`);
    await page.waitForFunction(() => window.__game?.ready);
    const sizes = () => page.evaluate(() => {
      const r = window.__render!, tiles = window.__game!.tiles as any;
      const resolution = tiles.tiles.cameraMap.get(r.camera);
      return { canvas: [r.renderer.domElement.width, r.renderer.domElement.height], sse: [resolution.x, resolution.y] };
    });
    await expect.poll(async () => (await sizes()).sse).toEqual([1800, 1200]);
    expect((await sizes()).canvas).toEqual([1800, 1200]);
    await page.setViewportSize({ width: 1600, height: 1000 });
    await expect.poll(async () => (await sizes()).canvas[0]).toBeGreaterThan(2000);
    const resized = await sizes();
    expect(resized.sse).toEqual(resized.canvas);
    expect(resized.canvas[0] * resized.canvas[1]).toBeLessThanOrEqual(2560 * 1440);
    const elevation = await page.evaluate(() => window.__render!.environment.sunElevation);
    const slider = page.getByRole('slider', { name: 'Time of day (UTC minutes)' });
    // Playwright's fill() dispatches both input and change for range controls.
    await slider.fill('360');
    await expect.poll(() => page.evaluate(() => window.__render!.environment.sunElevation)).toBeLessThan(elevation);
    await page.keyboard.press('KeyH'); await expect(slider).toBeHidden();
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});
