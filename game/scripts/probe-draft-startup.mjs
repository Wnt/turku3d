// Manual startup evidence for the draft-only runtime; no static tile fallback or full browser suite.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { MAT } from '../src/shared/col/materials.ts';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const [url = 'https://wnt.github.io/turku-game/game/', out = path.join(reportsRoot, 'draft-startup')] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
const started = Date.now();
const browser = await chromium.launch({ executablePath: chromeExecutable(), headless: true,
  ignoreDefaultArgs: chromeIgnoreDefaultArgs, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
const requests = [], consoleErrors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.on('request', request => requests.push(request.url()));
  page.on('pageerror', error => consoleErrors.push(error.message));
  await page.goto(url);
  try {
    await page.waitForFunction(() => window.__game?.ready || window.__qaError, undefined, { timeout: 180_000 });
    if (!await page.evaluate(() => window.__qaError)) await page.waitForFunction(
      () => window.__game?.state?.().grounded, undefined, { timeout: 10_000 });
  }
  finally {
    const state = await page.evaluate(async () => {
      const game = window.__game, state = game?.state?.(), at = state?.vehicle?.pos;
      const carSupport = at ? await game.physics.raycast({ x: at[0], y: 500, z: at[2] }, { x: 0, y: -1, z: 0 }, 1000) : null;
      return { carSupport, ready: window.__game?.ready ?? false,
      error: window.__qaError ?? null, load: window.__game?.loadProgress?.(), state };
    });
    const report = { url, seconds: (Date.now() - started) / 1000, ...state, consoleErrors,
      fullTileRequests: requests.filter(value => /\/tiles\/|\/tileset\.json(?:\?|$)/.test(new URL(value).pathname)),
      draftRequests: requests.filter(value => value.includes('/coarse/')).length };
    fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(report, null, 2));
    await page.screenshot({ path: path.join(out, 'startup.png') });
    console.log(JSON.stringify(report));
    assert.equal(report.error, null);
    assert.equal(report.ready, true);
    assert.deepEqual(report.fullTileRequests, []);
    assert.ok(report.draftRequests > 0);
    assert.deepEqual(consoleErrors, []);
    assert.notEqual(report.state?.groundMaterial, MAT.roof, 'player start is on a roof');
    assert.ok(report.carSupport && [MAT.road, MAT.crossing, MAT.cobblestone, MAT.paving_slab, MAT.gravel, MAT.bridge_deck, MAT.ramp].includes(report.carSupport.material), 'own car must stand on a drivable surface');
  }
} finally { await browser.close(); }
