// One navigation per requested view. No response interception, tile export or network archive.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { main as renderGame } from '../../qa/render/render.mjs';
import { isolateBrowser } from '../dev/environment.mjs';
const require = createRequire(new URL('../../qa/render/package.json', import.meta.url));
const { chromium } = require('playwright');
isolateBrowser();

const file = path.resolve(process.argv[2]);
const out = path.dirname(file);
const record = JSON.parse(fs.readFileSync(file, 'utf8'));
const args = ['--use-angle=metal', '--ignore-gpu-blocklist', '--enable-gpu', '--enable-webgl', '--enable-unsafe-webgpu'];
let browser;
try {
  browser = await chromium.launch({ channel: 'chrome', headless: true, args });
  record.browser = { version: browser.version(), headless: true, args };
  const context = await browser.newContext({
    viewport: { width: record.width, height: record.height }, deviceScaleFactor: 1,
    locale: 'en-US', serviceWorkers: 'block',
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
  await page.goto(record.google_url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  const reject = page.getByRole('button', { name: 'Reject all', exact: true });
  // Wait for either consent or Maps rather than sleeping past a consent page.
  await page.waitForFunction(() => document.querySelector('canvas') ||
    [...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Reject all'));
  if (await reject.isVisible()) {
    await reject.click();
    record.consent = 'Reject all';
  } else record.consent = 'not presented';
  await page.locator('canvas').first().waitFor();
  await page.waitForTimeout(record.settle_seconds * 1000);
  record.google_final_url = page.url();
  record.google_ui = await page.locator('body').innerText();
  record.browser.webgl = await page.evaluate(() => {
    const gl = document.createElement('canvas').getContext('webgl2');
    const ext = gl?.getExtension('WEBGL_debug_renderer_info');
    return gl && ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null;
  });
  // A 3D URL alone is not evidence: require the active tilted-view control as well.
  const tilt = page.getByRole('button', { name: 'Tilt the view', exact: true });
  record.google_3d_control = await tilt.isVisible() ? await tilt.innerText() : null;
  const aerial = /@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(\d+(?:\.\d+)?)a,(\d+(?:\.\d+)?)y,(-?\d+(?:\.\d+)?)h,(\d+(?:\.\d+)?)t/.exec(page.url());
  if (!aerial || record.google_3d_control?.trim() !== '2D') {
    await page.screenshot({ path: path.join(out, 'google-fallback.png') });
    throw new Error('Google did not retain a tilted 3D scene; local fallback screenshot saved. No comparison claimed.');
  }
  record.google_effective_pose = Object.fromEntries(['lat', 'lon', 'altitude', 'fov', 'heading', 'tilt'].map((k, i) => [k, Number(aerial[i + 1])]));
  record.google_pose_delta = {};
  for (const [key, value] of Object.entries(record.google_effective_pose)) {
    let delta = value - record.google_pose[key];
    if (key === 'heading') delta = ((delta + 180) % 360 + 360) % 360 - 180;
    record.google_pose_delta[key] = delta;
    const tolerance = ['lat', 'lon'].includes(key) ? 1e-6 : 0.05;
    if (Math.abs(delta) > tolerance) throw new Error(`Google changed requested ${key} by ${delta}; no matching pose claimed`);
  }
  // Hide interactive overlays, keeping the map canvas, Google logo and attribution footer intact.
  await page.addStyleTag({ content: '#omnibox-container, #app-container [role="navigation"], #minimap, #vasquette { visibility: hidden !important; }' });
  await page.screenshot({ path: path.join(out, 'google.png'), animations: 'disabled' });
  await context.close();
  const failures = await renderGame([
    '--views', path.join(out, 'views.jsonl'), '--url', record.game_url,
    '--out', out, '--pass', 'color', '--extra', 'hud=0',
  ], browser);
  if (failures) throw new Error('Game QA render failed; see game.meta.json');
  record.status = 'captured-unverified';
} catch (error) {
  record.status = 'failed';
  record.error = String(error.message ?? error);
  console.error(record.error);
  process.exitCode = 1;
} finally {
  await browser?.close();
  fs.writeFileSync(file, JSON.stringify(record, null, 2) + '\n');
}
