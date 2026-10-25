import { test, expect } from '@playwright/test';

test('@render HiDPI SSE follows output pixels after resize and the debug time slider refreshes sunlight', async ({ browser }, info) => {
  const context = await browser.newContext({ viewport: { width: 900, height: 600 }, deviceScaleFactor: 2 });
  const page = await context.newPage();
  const session = await context.newCDPSession(page);
  await session.send('Emulation.setFocusEmulationEnabled', { enabled: true });
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

for (const aa of ['off', 'taa', 'fxaa']) {
  test(`@render AA diagnostic ${aa} renders without errors`, async ({ page }, info) => {
    await page.setViewportSize({ width: 480, height: 320 });
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    await page.goto(`/?backend=${info.project.name}&aa=${aa}&cam=23459668,6704254,8,0,-15,60`);
    await page.waitForFunction(() => window.__qaReady || window.__qaError);
    expect(await page.evaluate(() => window.__qaError)).toBeUndefined();
    expect(await page.evaluate(() => (window.__game!.params as any).aa)).toBe(aa);
    const result = await page.evaluate(async () => {
      const renderer = window.__render!.renderer, original = renderer.render.bind(renderer);
      const passes: string[] = [];
      renderer.render = (scene, camera) => { passes.push(scene.name); original(scene, camera); };
      try {
        for (let i = 0; i < 3; i++) await new Promise(requestAnimationFrame);
        const canvas = document.createElement('canvas');
        canvas.width = 480; canvas.height = 320;
        const context = canvas.getContext('2d')!;
        context.drawImage(renderer.domElement, 0, 0);
        const pixels = context.getImageData(0, 0, 480, 320).data;
        const colors = new Set<number>();
        for (let i = 0; i < pixels.length; i += 4) colors.add(pixels[i] << 16 | pixels[i + 1] << 8 | pixels[i + 2]);
        return { temporal: passes.includes('TRAA'), colors: colors.size };
      } finally { renderer.render = original; }
    });
    expect(result.temporal).toBe(aa === 'taa');
    expect(result.colors).toBeGreaterThan(100);
    expect(errors).toEqual([]);
  });
}
