// Car body damage on the deployed share (vehicle/README.md "Body damage"): the starter car from four views, fresh and
// after hits, with the crumple measured from the drawn body's deformed bounds. Drives a Chrome that session.py holds:
//   ~/turku3d/tools/host-lease/session.py run air car-damage --kind func --headless --load-timeout 180 \
//     -- node game/scripts/car-damage-probe.mjs dent OUT_DIR
// Modes: `dent` (developer dents, slice A), `wall` (drives into a wall, slice B), `mp` (a second client sees the damage).
// Writes OUT_DIR/result.json and PNGs; prints one JSON line.
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';

const mode = process.argv[2] ?? 'dent', out = path.resolve(process.argv[3] ?? 'car-damage-probe');
const url = process.env.PROBE_URL ?? 'https://city.lab.madekivi.fi/?dev=1&time=13:00&weather=clear';
fs.mkdirSync(out, { recursive: true });
const report = { mode, url, errors: [], shots: [], steps: [] }, t0 = Date.now();
const log = (step, data = {}) => { const entry = { t: +((Date.now() - t0) / 1000).toFixed(1), step, ...data }; report.steps.push(entry); console.error(JSON.stringify(entry)); };
const browser = await chromium.connectOverCDP(process.env.BROWSER_CDP, { timeout: 30000 });
const context = browser.contexts()[0] ?? await browser.newContext();
const VIEWS = [['front-left', 35], ['front-right', -35], ['left', 90], ['rear', 180]];

async function open(name) {
  const page = await context.newPage();
  await page.setViewportSize({ width: 960, height: 540 });
  page.on('pageerror', e => report.errors.push(`${name} pageerror: ${e.message}`.slice(0, 300)));
  page.on('console', m => { const t = m.text();
    if (/GPUValidationError|Invalid (ShaderModule|RenderPipeline|BindGroup)|WGSL|TypeError|ReferenceError/.test(t)) report.errors.push(`${name} console: ${t}`.slice(0, 300)); });
  await page.goto(`${url}&probe=${Date.now()}`, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await page.waitForFunction(() => window.__game?.ready, null, { timeout: 240000 });
  log(`${name} menu`);
  await page.getByText('Multiplayer', { exact: true }).click();
  await page.waitForFunction(() => { const s = window.__game.state?.(); return s && !s.startScreen && !s.loading && window.__game.damage?.bounds(); }, null, { timeout: 180000 });
  log(`${name} playing`, { release: await page.evaluate(() => fetch('/version.json').then(r => r.json()).then(v => v.sha).catch(() => null)) });
  return page;
}

/** Frames rendered after a state change, so the shot shows it. */
const settle = (page, frames = 20) => page.evaluate(n => new Promise(resolve => {
  let left = n; const tick = () => (--left <= 0 ? resolve() : requestAnimationFrame(tick)); requestAnimationFrame(tick);
}), frames);

async function shots(page, label, client = 'a') {
  for (const [view, yaw] of VIEWS) {
    await page.evaluate(yaw => window.__game.damage.look({ yaw, pitch: 14, distance: 6.2 }), yaw);
    await settle(page, 12);
    const file = path.join(out, `${client}-${label}-${view}.png`);
    await page.screenshot({ path: file });
    report.shots.push(file);
  }
  await page.evaluate(() => window.__game.damage.look(null));
}

const bounds = page => page.evaluate(() => ({ bounds: window.__game.damage.bounds(), state: window.__game.damage.state() }));

/** Mean frame time over `n` frames (ms) with the CPU time of the car's skinning update (skeleton + pose). */
const frames = (page, n = 120) => page.evaluate(n => new Promise(resolve => {
  const times = []; let last = performance.now();
  const tick = () => { const now = performance.now(); times.push(now - last); last = now;
    if (times.length >= n) { times.sort((a, b) => a - b); resolve({ meanMs: times.reduce((a, b) => a + b) / n, p50: times[n >> 1], p95: times[Math.floor(n * .95)] }); }
    else requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
}), n);

try {
  const page = await open('a');
  await page.evaluate(() => window.__game.damage.clear());
  await settle(page, 30);
  report.fresh = await bounds(page);
  report.framesFresh = await frames(page);
  await shots(page, 'fresh');
  if (mode === 'dent') {
    for (const [label, hits] of [['front80', [{ kmh: 80, at: 'front' }]],
      ['rear50-side50', [{ kmh: 50, at: 'rear' }, { kmh: 50, at: 'left' }]],
      ['pole60-corner', [{ kmh: 60, at: 'front-right', halfWidth: .15 }]]]) {
      for (const hit of hits) await page.evaluate(hit => window.__game.damage.dent(hit), hit);
      await settle(page, 20);
      report[label] = await bounds(page);
      log(label, report[label].bounds && { front: report[label].bounds.frontLoss, rear: report[label].bounds.rearLoss, width: report[label].bounds.widthGain });
      await shots(page, label);
    }
    report.framesDamaged = await frames(page);
  }
  report.ok = report.errors.length === 0;
} catch (error) {
  report.failure = String(error).slice(0, 500);
  report.ok = false;
} finally {
  report.seconds = (Date.now() - t0) / 1000;
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(report, null, 1));
  try { await (await browser.newBrowserCDPSession()).send('Browser.close'); } catch { /* session.py closes it too */ }
}
console.log(JSON.stringify({ ok: report.ok, failure: report.failure, errors: report.errors.slice(0, 5), out, seconds: report.seconds }));
process.exit(report.ok ? 0 : 1);
