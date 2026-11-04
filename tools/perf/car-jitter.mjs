// Pose discontinuity of the driven car in a real game (not the QA replay): takes a parked car next to the start,
// drives it with the autopilot on real physics, and records the drawn car and the camera every rendered frame.
//   CDP=http://127.0.0.1:9332 node tools/perf/car-jitter.mjs LABEL [URL] [SECONDS]
// URL defaults to https://city.lab.madekivi.fi/. Prints one JSON line and writes it to
// ~/turku3d/loops/carjitter/runs/LABEL.json. Run Chrome under a host lease (tools/host-lease); never Jonni's.
// Metrics per rendered frame (see game/tests/perf/carjitter.test.ts for the replay version):
//   world: acceleration of the drawn car (m/s²; a real car stays under ~10, a 1-step hold/jump at 15 m/s is ~900)
//   relative: acceleration of the drawn car relative to the camera
//   screen: second difference of the car's screen position (px at 1280 wide): how much the car shakes in view
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [label, url = 'https://city.lab.madekivi.fi/', seconds = '30'] = process.argv.slice(2);
if (!label || !process.env.CDP) throw new Error('Usage: CDP=http://127.0.0.1:PORT node tools/perf/car-jitter.mjs LABEL [URL] [SECONDS]');
const repo = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../..');
const { chromium } = await import(pathToFileURL(path.join(repo, 'game/node_modules/@playwright/test/index.mjs')).href);
const browser = await chromium.connectOverCDP(process.env.CDP);
const context = browser.contexts()[0] ?? await browser.newContext();
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message.slice(0, 300)));
await page.goto(`${url}${url.includes('?') ? '&' : '?'}car_jitter=${label}`, { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForFunction(() => window.__game?.ready, null, { timeout: 180000 });
const version = await page.evaluate(() => fetch('/version.json').then(r => r.json()).then(v => v.sha).catch(() => null));
for (let k = 0; k < 3 && await page.evaluate(() => window.__game.state().startScreen); k++) {
  await page.locator('[data-option="new"], [data-option="continue"]').first().click().catch(() => {});
  await page.waitForTimeout(1500);
  await page.locator('[data-option="yes"], [data-option="confirm"], [data-option="new"]').first().click({ timeout: 1000 }).catch(() => {});
}
await page.waitForFunction(() => !window.__game.state().startScreen && !window.__game.state().loading, null, { timeout: 240000 });
await page.waitForTimeout(8000);
// The nearest parked car with a body, as Jonni drove a taken car.
const taken = await page.evaluate(async () => {
  const g = window.__game, s = g.state(), [x, , z] = s.feet;
  for (let k = 0; k < 40; k++) {
    const car = (g.physics.latest?.vehicles ?? []).filter(v => v.id !== 'player' && !/scooter|truck/.test(v.id))
      .map(v => ({ id: v.id, d: Math.hypot(v.pos.x - x, v.pos.z - z), pos: v.pos })).sort((a, b) => a.d - b.d)[0];
    if (car && car.d < 120) { g.enterVehicle(car.id); return car; }
    await new Promise(r => setTimeout(r, 500));
  }
  return null;
});
if (!taken) throw new Error('No parked car with a body near the start');
await page.waitForFunction(() => window.__game.state().mode === 'car', null, { timeout: 10000 });
await page.evaluate(() => {
  const g = window.__game, r = window.__render, s = g.state(), [x, , z] = s.feet;
  g.autopilot.goTo({ x: x + 600, z: z - 400 }, { mode: 'drive', speed: 14 });
  const out = window.__jitter = { rows: [] };
  let lastFrame = r.renderer.info.frame, lastCall = performance.now();
  const before = r.beforeFrame;
  r.beforeFrame = () => {
    const now = performance.now(), frame = r.renderer.info.frame;
    // A frame was rendered since the last call: record what it drew, at the time it started.
    if (frame !== lastFrame) {
      const id = g.state().drivenId, seen = id ? g.parkedVisuals.inspect(id, r.camera) : null;
      if (seen) out.rows.push([lastCall, ...seen.position, ...r.camera.position.toArray(), seen.projected[0], seen.projected[1], g.state().vehicle?.speed ?? 0]);
    }
    lastFrame = frame; lastCall = now;
    return before ? before() : undefined;
  };
});
await page.waitForTimeout(Number(seconds) * 1000);
const rows = await page.evaluate(() => window.__jitter.rows);
await page.close();
await browser.close();

const pct = (a, q) => { const s = a.toSorted((x, y) => x - y); return +(s[Math.min(s.length - 1, Math.floor(q * s.length))] ?? 0).toFixed(2); };
const world = [], relative = [], screen = [];
for (let k = 1; k < rows.length - 1; k++) {
  const [a, b, c] = [rows[k - 1], rows[k], rows[k + 1]], d1 = (b[0] - a[0]) / 1000, d2 = (c[0] - b[0]) / 1000;
  if (b[10] < 3 || d1 <= 0 || d2 <= 0) continue;
  const acc = f => Math.hypot(...[0, 1, 2].map(i => ((f(c, i) - f(b, i)) / d2 - (f(b, i) - f(a, i)) / d1) / ((d1 + d2) / 2)));
  const step = Math.hypot(c[1] - b[1], c[3] - b[3]);
  if (step > 5) continue; // a reset
  world.push(acc((r, i) => r[1 + i]));
  relative.push(acc((r, i) => r[1 + i] - r[4 + i]));
  screen.push(Math.hypot((c[7] - 2 * b[7] + a[7]) * 640, (c[8] - 2 * b[8] + a[8]) * 360));
}
const frames = rows.slice(1).map((r, k) => r[0] - rows[k][0]);
const result = { label, version, url, taken: taken.id, frames: rows.length, moving: world.length, frameP50: pct(frames, 0.5), frameP99: pct(frames, 0.99),
  worldP50: pct(world, 0.5), worldP99: pct(world, 0.99), worldOver100: world.filter(a => a > 100).length,
  relativeP50: pct(relative, 0.5), relativeP99: pct(relative, 0.99), relativeOver100: relative.filter(a => a > 100).length,
  screenP50: pct(screen, 0.5), screenP99: pct(screen, 0.99), screenOver2px: screen.filter(s => s > 2).length, errors: errors.slice(0, 3) };
const dir = path.join(os.homedir(), 'turku3d/loops/carjitter/runs');
fs.mkdirSync(dir, { recursive: true });
fs.writeFileSync(path.join(dir, `${label}.json`), JSON.stringify({ result, rows }));
console.log(JSON.stringify(result));
