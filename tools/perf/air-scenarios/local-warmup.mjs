// Local real-WebGPU reproduction; always run through host_slots.py browser.
import { chromium } from '../../../game/node_modules/playwright-core/index.mjs';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../dev/chrome-args.mjs';
import { isolateBrowser } from '../../dev/environment.mjs';
import fs from 'node:fs';
isolateBrowser();
const [base, output, expectedBuilds = '0'] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: chromeExecutable(), headless: true,
  ignoreDefaultArgs: chromeIgnoreDefaultArgs, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => { errors.push(error.message); console.error(error.message); });
  await page.route('**/tile-warmup-probe', route => route.fulfill({ contentType: 'text/html',
    body: '<script type="module" src="/scripts/tile-warmup-probe.ts"></script>' }));
  await page.goto(new URL('/tile-warmup-probe', base).href);
  await page.waitForFunction(() => window.__warmupResult, null, { timeout: 120000 });
  const result = { ...await page.evaluate(() => window.__warmupResult), errors };
  fs.writeFileSync(output, JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ preparedMs: result.preparedMs, firstDrawMs: result.firstDrawMs, publicationBuilds: result.publicationBuilds, publicationAoBuilds: result.publicationAoBuilds, warmedReceiveShadow: result.warmedReceiveShadow, errors }));
  if (errors.length) throw new Error(errors.join('\n'));
  if (Number(expectedBuilds) === 0 && result.publicationAoBuilds) throw new Error('Publication rebuilt the shared GTAO graph');
  if (result.publicationBuilds !== Number(expectedBuilds)) throw new Error(`Expected ${expectedBuilds} publication shader builds, got ${result.publicationBuilds}`);
} finally { await browser.close(); }
