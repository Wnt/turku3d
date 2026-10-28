import { chromeArgs, chromeIgnoreDefaultArgs } from '../../tools/dev/chrome-args.mjs';
// Small final-backend check for the native GPU completion waits; real-data timing uses preload-probe.mjs.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { isolateBrowser, port, reportsRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const out = path.join(reportsRoot, 'preload', 'gpu-smoke');
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ ignoreDefaultArgs: chromeIgnoreDefaultArgs, channel: 'chrome', headless: true,
  args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
try {
  const page = await browser.newPage({ viewport: { width: 960, height: 540 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(`http://127.0.0.1:${port(3)}/?scene=dev&backend=webgl&test`);
  await page.waitForFunction(() => window.__game?.ready || window.__qaError, null, { timeout: 40000 });
  const result = await page.evaluate(() => ({ backend: window.__game.backend,
    progress: window.__game.loadProgress(), error: window.__qaError ?? null }));
  assert.equal(result.error, null);
  assert.equal(result.backend, 'WebGL2');
  assert.equal(result.progress.phase, 'ready');
  assert.deepEqual(result.progress.compile, { done: 6, total: 6 });
  assert.deepEqual(errors, []);
  await page.screenshot({ path: path.join(out, 'webgl.png') });
  fs.writeFileSync(path.join(out, 'webgl.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} finally { await browser.close(); }
