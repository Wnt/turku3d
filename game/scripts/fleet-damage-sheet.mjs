// Body damage across the fleet (vehicle/README.md "Body damage across the fleet"): every fleet model in the vehicle
// gallery's crash test, shot after a scripted 50 km/h wall dent and as a wreck with its split parts hanging loose
// (front and rear 82 km/h, a 30 km/h knock on each side at the mirrors), plus each model's split report. Drives a
// Chrome that session.py holds:
//   ~/turku3d/tools/host-lease/session.py run air car-damage --kind func --headless --load-timeout 240 \
//     --url https://city.lab.madekivi.fi/vehicle-gallery.html -- node game/scripts/fleet-damage-sheet.mjs OUT_DIR
// Writes OUT_DIR/<model>-<shot>.png, OUT_DIR/report.json; prints one JSON line. `fleet-damage-sheet.py OUT_DIR` tiles
// the shots into sheets. ONLY=id,id limits the models.
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';

const out = path.resolve(process.argv[2] ?? 'fleet-damage-sheet');
const url = process.env.SHEET_URL ?? 'https://city.lab.madekivi.fi/vehicle-gallery.html';
const only = process.env.ONLY?.split(',').filter(Boolean);
fs.mkdirSync(out, { recursive: true });
const report = { url, errors: [], models: {} }, t0 = Date.now();
const log = (step, data = {}) => console.error(JSON.stringify({ t: +((Date.now() - t0) / 1000).toFixed(1), step, ...data }));
const browser = await chromium.connectOverCDP(process.env.BROWSER_CDP, { timeout: 30000 });
const context = browser.contexts()[0] ?? await browser.newContext();
const page = await context.newPage();
await page.setViewportSize({ width: 1280, height: 900 });
await page.addInitScript(() => Object.defineProperty(navigator, 'getGamepads', { value: () => [] }));
page.on('pageerror', e => report.errors.push(`pageerror: ${e.message}`.slice(0, 300)));
page.on('console', m => { const t = m.text();
  if (/GPUValidationError|Invalid (ShaderModule|RenderPipeline|BindGroup)|WGSL|TypeError|ReferenceError/.test(t)) report.errors.push(`console: ${t}`.slice(0, 300)); });
await page.goto(`${url}?sheet=${Date.now()}`, { waitUntil: 'domcontentloaded', timeout: 90000 });
await page.waitForFunction(() => window.vehicleGallery?.entries, null, { timeout: 120000 });
report.release = await page.evaluate(() => fetch('/version.json').then(r => r.json()).then(v => v.sha).catch(() => null));
const ids = (await page.evaluate(() => window.vehicleGallery.entries())).filter(id => !['car', 'scooter', 'truck'].includes(id) && (!only || only.includes(id)));
log('gallery', { models: ids.length, release: report.release });

const frames = (n = 20) => page.evaluate(n => new Promise(resolve => {
  let left = n; const tick = () => (--left <= 0 ? resolve() : requestAnimationFrame(tick)); requestAnimationFrame(tick);
}), n);
async function shot(id, name, view) {
  await page.evaluate(v => { window.vehicleGallery.view = v; }, view);
  await frames(24);
  const file = path.join(out, `${id}-${name}.png`);
  await page.locator('.view.active').screenshot({ path: file });
  return file;
}

for (const id of ids) {
  const started = Date.now();
  try {
    const ok = await page.evaluate(id => window.vehicleGallery.select(id), id);
    if (!ok) throw Error('not activated');
    await page.evaluate(() => { window.vehicleGallery.hold = { throttle: 0, steer: 0, brake: 0, handbrake: true }; window.vehicleGallery.clearDamage(); });
    await frames(10);
    const box = await page.evaluate(() => window.vehicleGallery.damage()?.box);
    if (!box) throw Error('no damage rig');
    const d = Math.max(5.2, box.length * 1.25), height = box.height * .35;
    const front = { yaw: 38, pitch: 12, distance: d, height }, rear = { yaw: 215, pitch: 12, distance: d, height };
    await page.evaluate(() => window.vehicleGallery.crash({ kmh: 50, push: [0, 0, -1] }));
    const dent = await shot(id, 'dent50', front);
    const after50 = await page.evaluate(() => window.vehicleGallery.damage());
    // The wreck: a fresh body, then the hits that hang every split part loose without tearing any off.
    await page.evaluate(b => {
      const g = window.vehicleGallery; g.clearDamage();
      g.crash({ kmh: 82, push: [0, 0, -1] }); g.crash({ kmh: 82, push: [0, 0, 1] });
      g.crash({ kmh: 30, push: [-1, 0, 0], at: [b.width / 2, .6, .15 * b.length], halfWidth: .4 });
      g.crash({ kmh: 30, push: [1, 0, 0], at: [-b.width / 2, .6, .15 * b.length], halfWidth: .4 });
    }, box);
    await page.waitForTimeout(1500);
    const wreckFront = await shot(id, 'wreck-front', front), wreckRear = await shot(id, 'wreck-rear', rear);
    const wreck = await page.evaluate(() => window.vehicleGallery.damage());
    await page.evaluate(() => { window.vehicleGallery.view = null; window.vehicleGallery.hold = null; window.vehicleGallery.clearDamage(); });
    report.models[id] = { seconds: (Date.now() - started) / 1000, parts: after50.parts, fit: after50.fit, glass: after50.glass, box,
      dent50: { hits: after50.hits, slot: after50.slot }, wreck: { hits: wreck.hits, parts: wreck.partStates }, shots: [dent, wreckFront, wreckRear] };
    log('model', { id, seconds: report.models[id].seconds, wreck: wreck.partStates });
  } catch (error) {
    report.models[id] = { error: String(error?.message ?? error) };
    log('model failed', { id, error: String(error?.message ?? error) });
  }
}
report.seconds = (Date.now() - t0) / 1000;
fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 1));
await page.close();
console.log(JSON.stringify({ models: Object.keys(report.models).length, failed: Object.entries(report.models).filter(([, m]) => m.error).map(([id]) => id),
  errors: report.errors.length, seconds: report.seconds, release: report.release }));
try { await (await browser.newBrowserCDPSession()).send('Browser.close'); } catch { /* session.py closes it too */ }
process.exit(0);
