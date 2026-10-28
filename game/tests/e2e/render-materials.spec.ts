import { reportsRoot } from '../../../tools/dev/environment.mjs';
import { isolatedTest as test, expect, type Page } from './fixtures';
import type { Scene, RenderTarget } from 'three/webgpu';
import fs from 'node:fs';

async function pixels(page: Page, png: Buffer) {
  return page.evaluate(async base64 => {
    const image = new Image(); image.src = `data:image/png;base64,${base64}`; await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const ctx = canvas.getContext('2d')!; ctx.drawImage(image, 0, 0);
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    // Sampling the entire frame checks colour/ID output without returning megabytes through Playwright.
    const sampled = [];
    for (let i = 0; i < data.length; i += 4 * 97) sampled.push((data[i] << 16) | (data[i + 1] << 8) | data[i + 2]);
    return sampled;
  }, png.toString('base64'));
}

for (const quality of ['low', 'medium', 'high']) {
  test(`@render ${quality}: material shaders, planar river, time edit and exact QA IDs`, async ({ page }, info) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    await page.setViewportSize({ width: 1280, height: 720 });
    const url = `/?scene=dev&backend=${info.project.name}&quality=${quality}&tiles=render-fixture/tileset.json` +
      '&cam=23459668,6704252,7,0,-12,60&time=2026-10-15T11:00:00Z';
    await page.goto(url);
    await page.waitForFunction(() => window.__qaReady || window.__qaError);
    expect(await page.evaluate(() => window.__qaError)).toBeUndefined();
    fs.mkdirSync(`${reportsRoot}/render/materials`, { recursive: true });
    const screenshot = await page.screenshot({ path: `${reportsRoot}/render/materials/${info.project.name}-${quality}.png` });
    await info.attach(`${quality}-materials`, { body: screenshot, contentType: 'image/png' });
    expect(new Set(await pixels(page, screenshot)).size).toBeGreaterThan(100);
    const state = await page.evaluate(async () => {
      const env = window.__render!.environment;
      // Read the actual filtered sky pixels, not just texture identity: time edits reuse the render target.
      const target = (env as unknown as { environmentTarget: RenderTarget }).environmentTarget;
      const pixels = async () => {
        const data = await window.__render!.renderer.readRenderTargetPixelsAsync(target, 0, 0, target.width, target.height);
        let hash = 2166136261;
        for (const byte of new Uint8Array(data.buffer, data.byteOffset, data.byteLength)) hash = Math.imul(hash ^ byte, 16777619);
        return hash;
      };
      const beforePixels = await pixels();
      const before = { elevation: env.sunElevation, texture: (env.sky.parent as Scene).environment?.uuid };
      window.__render!.setTime('2026-10-15T06:00:00Z');
      const afterPixels = await pixels();
      const after = { elevation: env.sunElevation, texture: (env.sky.parent as Scene).environment?.uuid };
      window.__render!.setTime('2026-10-15T06:00:00Z');
      return { before, after, beforePixels, afterPixels, repeatedPixels: await pixels(), repeatedTexture: (env.sky.parent as Scene).environment?.uuid };
    });
    expect(state.after.elevation).toBeLessThan(state.before.elevation);
    expect(state.after.texture).toBe(state.before.texture);
    expect(state.afterPixels).not.toBe(state.beforePixels);
    expect(state.repeatedPixels).toBe(state.afterPixels);
    expect(state.repeatedTexture).toBe(state.after.texture);

    await page.goto(`${url}&pass=id`);
    await page.waitForFunction(() => window.__qaReady || window.__qaError);
    expect(await page.evaluate(() => window.__qaError)).toBeUndefined();
    const table = await page.evaluate(() => window.__qaIdTable!);
    expect(Object.values(table).some((v: any) => v.kind === 'water')).toBe(true);
    const ids = new Set(await pixels(page, await page.screenshot()));
    expect(ids.size).toBeGreaterThan(3);
    for (const id of ids) if (id) expect(table[String(id)], `unlisted pixel ID ${id}`).toBeDefined();
    expect(errors).toEqual([]);
  });
}
