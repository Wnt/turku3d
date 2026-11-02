import { isolatedTest as test, expect } from './fixtures';

test('@render the canvas follows HiDPI output pixels after resize; the developer time panel moves the sun locally', async ({ browser }, info) => {
  const context = await browser.newContext({ viewport: { width: 900, height: 600 }, deviceScaleFactor: 2 });
  await context.addInitScript(() => Object.defineProperty(navigator, 'getGamepads', { value: () => [] }));
  const page = await context.newPage();
  const session = await context.newCDPSession(page);
  await session.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  try {
    await page.goto(`/?scene=dev&backend=${info.project.name}`);
    await page.getByRole('option', { name: 'New game' }).click();
    await page.getByRole('button', { name: 'Skip' }).click();
    await page.waitForFunction(() => window.__game?.ready);
    // Output pixels (the draft tiles have no screen-space error of their own since the 3D Tiles renderer left).
    const canvas = () => page.evaluate(() => { const c = window.__render!.renderer.domElement; return [c.width, c.height]; });
    await expect.poll(canvas).toEqual([1800, 1200]);
    await page.setViewportSize({ width: 1600, height: 1000 });
    await expect.poll(async () => (await canvas())[0]).toBeGreaterThan(2000);
    const resized = await canvas();
    expect(resized[0] * resized[1]).toBeLessThanOrEqual(2560 * 1440);
    // The developer time panel (ui/timePanel.ts; localhost runs the developer tools): T shows it, the hour slider moves
    // the sun at once and freezes a local view with a badge, 4 runs it at ×600, B returns to the live clock.
    const sun = () => page.evaluate(() => window.__render!.environment.sunElevation);
    const hour = () => page.evaluate(() => (window.__game!.dayClock as { hour: number }).hour);
    const badge = page.locator('#local-time-badge');
    await expect(badge).toBeHidden();
    await page.keyboard.press('KeyT');
    const slider = page.getByRole('slider', { name: 'Clock time' });
    await expect(slider).toBeVisible();
    // Playwright's fill() dispatches both input and change for range controls.
    await slider.fill('780');
    await expect.poll(sun).toBeGreaterThan(30);
    await slider.fill('1320');
    await expect.poll(sun).toBeLessThan(-12);
    await expect(page.locator('#time-panel .time-readout')).toHaveText('22:00 · sun −13.4° · night 1.00 · paused');
    await expect(badge).toHaveText(/^Local time · paused · live \d\d:\d\d$/);
    // The focused slider keeps its arrow keys, but the panel's shortcuts still reach the game.
    await page.keyboard.press('Digit4');
    await expect(badge).toHaveText(/^Local time ×600 · live/);
    await expect.poll(hour).toBeGreaterThan(22.3);
    await page.keyboard.press('Digit0');
    await expect(badge).toHaveText(/ · paused · /);
    await page.keyboard.press('KeyB');
    await expect(badge).toBeHidden();
    await expect(page.locator('#time-panel .time-readout')).toHaveText(/ · live$/);
    await page.keyboard.press('KeyT'); await expect(slider).toBeHidden();
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});

for (const aa of ['off', 'taa', 'fxaa']) {
  test(`@render AA diagnostic ${aa} renders without errors`, async ({ page }, info) => {
    await page.setViewportSize({ width: 480, height: 320 });
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    await page.goto(`/?scene=dev&backend=${info.project.name}&aa=${aa}&cam=23459668,6704254,8,0,-15,60`);
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
