import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
// Capture the registry poses and a synthetic street view on both backends.
// Usage: npm run render-qa -- [preview-url] [output-directory]; defaults to sandbox preview and reports.
import { port, reportsRoot, isolateBrowser } from '../../tools/dev/environment.mjs';
import { chromium } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

isolateBrowser();
const [base = `http://localhost:${port(1)}/`, out = path.join(reportsRoot, 'render/after')] = process.argv.slice(2);
await fs.mkdir(out, { recursive: true });
const views = (await fs.readFile(new URL('../../qa/views/views.jsonl', import.meta.url), 'utf8'))
  .trim().split('\n').map(JSON.parse);
views.push({ id: 'dev_street', width: 1280, height: 960,
  pose: { E: 23459540, N: 6704290, H: 3.7, yaw: 40, pitch: 2, fov: 75, roll: 0 } });
const browser = await chromium.launch( { ignoreDefaultArgs: chromeIgnoreDefaultArgs, executablePath: chromeExecutable(), headless: true,
  args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
const context = await browser.newContext({ deviceScaleFactor: 1 });
const results = [];
try {
  for (const backend of ['webgpu', 'webgl']) for (const view of views) {
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    await page.setViewportSize({ width: view.width, height: view.height });
    const url = new URL(base), p = view.pose;
    url.searchParams.set('backend', backend);
    if (view.id === 'dev_street') {
      url.searchParams.delete('tiles');
      url.searchParams.delete('manifest');
      url.searchParams.set('scene', 'dev');
    }
    url.searchParams.set('cam', [p.E, p.N, p.H, p.yaw, p.pitch, p.fov].join(','));
    url.searchParams.set('roll', p.roll);
    url.searchParams.set('time', '2026-10-15T14:00:00+03:00');
    await page.goto(url.href);
    await page.waitForFunction(() => window.__qaReady || window.__qaError);
    const state = await page.evaluate(() => ({ error: window.__qaError, pose: window.__qaPose, info: window.__qaInfo }));
    if (state.error) throw new Error(state.error);
    const file = `${backend}-${view.id}.png`;
    await page.screenshot({ path: path.join(out, file) });
    results.push({ view: view.id, backend, url: url.href, file, errors, ...state });
    console.log(`${backend} ${view.id}: ${errors.length ? errors.join('; ') : 'OK'}`);
    await page.close();
  }
} finally {
  await browser.close();
  await fs.writeFile(path.join(out, 'manifest.json'), JSON.stringify({
    commit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(), results,
  }, null, 2));
}
if (results.some(r => r.errors.length)) process.exitCode = 1;
