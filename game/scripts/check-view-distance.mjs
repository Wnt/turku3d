// Selective deployed-client evidence: real developer UI, real OCI scenery, then release it.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const out = path.join(reportsRoot, 'view-distance'); fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs,
  headless: true, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
const deadline = setTimeout(() => { void browser.close(); }, 480_000);
const report = { errors: [], requests: [] };
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('pageerror', error => report.errors.push(error.message));
  page.on('request', request => { if (/\/coarse\/t0_.*\/manifest.json/.test(request.url())) report.requests.push(request.url()); });
  await page.goto('https://wnt.github.io/turku-game/game/?dev=1', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.__game?.ready, null, { timeout: 150_000 });
  await page.getByRole('option', { name: 'New game', exact: true }).click();
  const skip = page.getByRole('button', { name: 'Skip', exact: true });
  await skip.waitFor({ timeout: 15_000 }); await skip.click();
  await page.waitForFunction(() => !window.__game.state().startScreen && !window.__game.state().loading, null, { timeout: 60_000 });
  await page.locator('#dev-layers > button').click();
  const select = page.locator('#dev-view-distance');
  assert.equal(await select.inputValue(), '0');
  await select.selectOption('3000');
  await page.waitForFunction(() => window.__game.coarse.screenshotScenery.radius === 3000);
  report.extended = await page.evaluate(() => ({ far: window.__render.camera.far,
    progress: window.__game.coarse.screenshotScenery.progress, collisionRadius: window.__game.streamer.radius }));
  assert.ok(report.extended.far >= 12000);
  await select.selectOption('1000');
  await page.waitForFunction(() => window.__game.coarse.screenshotScenery.progress.loaded >= 4, null, { timeout: 180_000 });
  report.loaded = await page.evaluate(() => ({ progress: window.__game.coarse.screenshotScenery.progress,
    collisionRadius: window.__game.streamer.radius,
    scenery: [...window.__game.coarse.screenshotScenery.entries].filter(([, e]) => e.model).map(([id]) => id),
    near: [...window.__game.coarse.entries].filter(([, e]) => e.model).map(([id]) => id) }));
  assert.equal(report.loaded.collisionRadius, report.extended.collisionRadius);
  assert.ok(report.loaded.scenery.some(id => !report.loaded.near.includes(id)));
  await page.screenshot({ path: path.join(out, 'extended.png') });
  await select.selectOption('0');
  report.reset = await page.evaluate(() => ({ progress: window.__game.coarse.screenshotScenery.progress,
    far: window.__render.camera.far }));
  assert.equal(report.reset.progress.total, 0); assert.equal(report.reset.far, 6000);
  await page.screenshot({ path: path.join(out, 'normal.png') });
  assert.deepEqual(report.errors, []);
  report.ok = true;
} finally {
  clearTimeout(deadline); fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(report, null, 2));
  await browser.close(); console.log(JSON.stringify(report));
}
