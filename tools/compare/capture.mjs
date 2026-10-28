// Local reference viewing only: no response interception, tile export or network archive.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { contractUrl, poseEcho } from '../../qa/render/render.mjs';
import { isolateBrowser } from '../dev/environment.mjs';
const require = createRequire(new URL('../../qa/render/package.json', import.meta.url));
const { chromium } = require('playwright');
isolateBrowser();
const args = ['--window-size=1280,980', '--use-angle=metal', '--ignore-gpu-blocklist', '--enable-gpu', '--enable-webgl', '--enable-unsafe-webgpu', '--disk-cache-size=1', '--media-cache-size=1'];

async function disableCache(context, page) {
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
}

export async function consent(context, page) {
  const known = (await context.cookies('https://www.google.com')).filter(c => ['SOCS', 'CONSENT'].includes(c.name));
  if (!known.length) {
    await page.goto('https://www.google.com/?hl=en', { waitUntil: 'domcontentloaded' });
    const reject = page.getByRole('button', { name: 'Reject all', exact: true });
    await reject.waitFor({ timeout: 15000 });
    await reject.click();
    await page.waitForFunction(() => ![...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Reject all'));
  }
  // Record presence/expiry, never cookie values. The dedicated profile is never signed in.
  const cookies = (await context.cookies('https://www.google.com')).filter(c => ['SOCS', 'CONSENT'].includes(c.name));
  if (!cookies.length) throw new Error('Consent rejection did not persist; refusing to continue');
  return cookies.map(c => ({ name: c.name, persistent: c.expires > 0 }));
}

export async function googleView(page, url, settleSeconds) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.locator('canvas').first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(1500);
  const tilt = page.getByRole('switch', { name: 'Tilt the view', exact: true });
  if (!await tilt.isVisible()) {
    await page.getByRole('button', { name: 'Layers', exact: true }).hover();
    await page.getByRole('button', { name: 'More', exact: true }).click();
    const globe = page.getByRole('checkbox', { name: 'Globe view', exact: true });
    if (await globe.getAttribute('aria-checked') !== 'true') await globe.click();
    await tilt.waitFor({ timeout: 20000 });
    await page.getByRole('button', { name: 'Close', exact: true }).last().click();
  }
  if (await tilt.getAttribute('aria-checked') !== 'true') await tilt.click();
  await page.mouse.move(640, 450); // dismiss the hovered Layers popup
  await page.waitForTimeout(settleSeconds * 1000);
  if (await tilt.getAttribute('aria-checked') !== 'true' || !/\d+a,.*\d+t\/data=/.test(page.url())) {
    throw new Error('Google did not retain a tilted 3D scene');
  }
  return page.url();
}

export async function main(file, shared = {}) {
  file = path.resolve(file);
  const out = path.dirname(file), record = JSON.parse(fs.readFileSync(file, 'utf8'));
  let context = shared.context, page = shared.page, game = shared.game;
  try {
    if (!context) {
      const profile = path.join(process.env.TURKU3D_BROWSER_DIR, 'gmaps-compare');
      context = await chromium.launchPersistentContext(profile, {
        channel: 'chrome', headless: false, viewport: { width: record.width, height: record.height },
        deviceScaleFactor: 1, locale: 'en-US', serviceWorkers: 'block', args,
      });
    }
    page ??= context.pages()[0] ?? await context.newPage();
    page.setDefaultTimeout(15000);
    await disableCache(context, page);
    await page.bringToFront();
    record.browser = { version: context.browser()?.version(), headless: false, args };
    record.consent = await consent(context, page);
    record.google_final_url = await googleView(page, record.google_url, record.settle_seconds);
    const converted = JSON.parse(execFileSync(record.python, [fileURLToPath(new URL('./gmaps_compare.py', import.meta.url)),
      '--google-url', record.google_final_url, '--height-datum', record.height_reference.datum,
      '--height-offset', String(record.height_reference.offset_m), '--url', record.game_url, '--pose-only'], { encoding: 'utf8' }));
    record.requested_game_pose = record.game_pose;
    record.requested_google_pose = record.google_pose;
    record.game_pose = converted.game_pose;
    record.google_pose = converted.google_pose;
    record.game_url = converted.game_url;
    fs.writeFileSync(path.join(out, 'views.jsonl'), JSON.stringify({ id: 'game', pose: record.game_pose, width: record.width, height: record.height }) + '\n');
    record.google_pose_delta = Object.fromEntries(Object.entries(record.google_pose).map(([k, v]) => [k, v - record.requested_google_pose[k]]));
    record.google_pose_delta.heading = ((record.google_pose_delta.heading + 180) % 360 + 360) % 360 - 180;
    record.browser.webgl = await page.evaluate(() => {
      const gl = document.createElement('canvas').getContext('webgl2');
      const ext = gl?.getExtension('WEBGL_debug_renderer_info');
      return gl && ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null;
    });
    // Only interactive overlays; preserve Google logo and the attribution/footer.
    record.google_overlays = 'Map labels/road vectors not verified off; UI hiding is partial';
    const style = await page.addStyleTag({ content: '[role="search"], [role="search"] *, .XltNde, .XltNde *, .HDs3Md, .HDs3Md *, .rHNip, .rHNip *, .DMPNfb, .DMPNfb * { visibility: hidden !important; }' });
    await page.screenshot({ path: path.join(out, 'google.png'), animations: 'disabled' });
    await style.evaluate(el => el.remove());
    game ??= await context.newPage();
    await game.bringToFront();
    const view = { pose: record.game_pose, width: record.width, height: record.height };
    await game.goto(contractUrl(record.game_url, view, { extra: 'hud=0' }), { waitUntil: 'domcontentloaded', timeout: 60000 });
    await game.waitForFunction(() => window.__qaReady === true || typeof window.__qaError === 'string', null, { timeout: 120000 });
    record.game_qa = await game.evaluate(() => ({ ready: window.__qaReady, error: window.__qaError, pose: window.__qaPose, info: window.__qaInfo }));
    const mismatch = poseEcho(view, record.game_qa.pose);
    if (record.game_qa.error || mismatch) throw new Error(record.game_qa.error || mismatch);
    await game.screenshot({ path: path.join(out, 'game.png'), animations: 'disabled' });
    record.status = 'captured-unverified';
    await page.bringToFront();
  } catch (error) {
    record.status = 'failed'; record.error = String(error.message ?? error);
    if (page && !page.isClosed()) await page.screenshot({ path: path.join(out, 'google-fallback.png') }).catch(() => {});
    console.error(record.error);
  } finally {
    fs.writeFileSync(file, JSON.stringify(record, null, 2) + '\n');
    if (!shared.context) await context?.close();
  }
  return record.status === 'captured-unverified' ? 0 : 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main(process.argv[2]);
}
