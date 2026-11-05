// Takes a moving traffic car in a solo game and drives it straight, then through a curve, logging per rendered frame:
// steer input, physics yaw rate, which path draws the car (its driven actor, or the claimed-car fallback that draws
// the raw physics body) and the drawn pose's acceleration.
//   session.py run mac NAME --kind func --headless --url 'http://127.0.0.1:5220/' -- node tools/perf/taken-car.mjs LABEL
// (BROWSER_CDP from session.py). Prints one JSON line; rows go to ~/turku3d/loops/carjitter/runs/LABEL.json.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [label] = process.argv.slice(2);
if (!label || !process.env.BROWSER_CDP) throw new Error('Usage: BROWSER_CDP=... node tools/perf/taken-car.mjs LABEL');
const repo = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../..');
const { chromium } = await import(pathToFileURL(path.join(repo, 'game/node_modules/@playwright/test/index.mjs')).href);
const browser = await chromium.connectOverCDP(process.env.BROWSER_CDP);
const context = browser.contexts()[0];
const page = context.pages().find(p => p.url().startsWith('http')) ?? context.pages()[0];
const step = what => console.error(`[taken-car ${label}] ${what}`);
const LIMIT = 90000, t0 = Date.now(), left = () => Math.max(1000, LIMIT - (Date.now() - t0));
await page.waitForFunction(() => window.__game?.ready, null, { timeout: left() });
for (let k = 0; k < 6 && await page.evaluate(() => window.__game.state().startScreen); k++) {
  await page.locator('[data-option="new"], [data-option="continue"]').first().click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(1500);
}
await page.waitForFunction(() => !window.__game.state().startScreen && !window.__game.state().loading, null, { timeout: left() });
step('playing');
// Wait for a moving traffic car within reach, then take it the way the interact key does.
const taken = await page.evaluate(async () => {
  const g = window.__game;
  for (let k = 0; k < 120; k++) {
    // The nearest moving traffic car (claiming needs no reach; the interact key adds a 4 m one).
    const [x, , z] = g.state().feet, cars = g.traffic.debugCars().cars
      .map(c => ({ ...c, d: Math.hypot(c.x - x, c.z - z), v: Math.hypot(c.vx, c.vz) })).filter(c => c.v > 3).sort((a, b) => a.d - b.d);
    if (cars.length) {
      const id = await g.traffic.enter(cars[0].handle);
      if (id) { g.enterVehicle(id); return { id, distance: +cars[0].d.toFixed(1), speed: +cars[0].v.toFixed(1) }; }
    }
    await new Promise(r => setTimeout(r, 500));
  }
  return null;
});
if (!taken) throw new Error('No traffic car could be taken within 60 s');
step(`took ${taken.id}`);
const rows = await page.evaluate(id => new Promise(done => {
  const g = window.__game, r = window.__render, original = g.input.update.bind(g.input), start = performance.now();
  // 3 s straight at full throttle, 2 s full right, 2 s straight, 2 s full left, then coast.
  const plan = t => t < 3000 ? [1, 0] : t < 5000 ? [0.6, 1] : t < 7000 ? [0.6, 0] : t < 9000 ? [0.6, -1] : [0, 0];
  g.input.update = dt => {
    original(dt);
    const [throttle, steer] = plan(performance.now() - start);
    g.input.actions.throttle = throttle; g.input.actions.steer = steer; g.input.actions.brake = 0;
  };
  const out = []; let lastFrame = r.renderer.info.frame, lastCall = performance.now();
  const before = r.beforeFrame;
  r.beforeFrame = () => {
    const now = performance.now(), frame = r.renderer.info.frame;
    if (frame !== lastFrame) {
      const raw = g.physics.latest?.vehicles.find(v => v.id === id), actor = g.parkedVisuals.renders(id) && g.parkedVisuals.inspect(id, r.camera);
      if (raw) {
        const yaw = Math.atan2(2 * (raw.rot.w * raw.rot.y + raw.rot.x * raw.rot.z), 1 - 2 * (raw.rot.y ** 2 + raw.rot.z ** 2));
        // The drawn car: its driven actor, or (fallback) the raw body that traffic/client.ts draws for it.
        const drawn = actor ? actor.position : [raw.pos.x, raw.pos.y - 0.75, raw.pos.z];
        out.push([lastCall, g.input.actions.steer, yaw, raw.speed, actor ? 1 : 0, ...drawn, ...r.camera.position.toArray()]);
      }
    }
    lastFrame = frame; lastCall = now;
    return before ? before() : undefined;
  };
  setTimeout(() => { r.beforeFrame = before; g.input.update = original; done(out); }, 11000);
}), taken.id);
await browser.close().catch(() => {});

const pct = (a, q) => { const s = a.toSorted((x, y) => x - y); return +(s[Math.min(s.length - 1, Math.floor(q * s.length))] ?? 0).toFixed(2); };
const world = [], relative = [], yawRate = [];
for (let k = 1; k < rows.length - 1; k++) {
  const [a, b, c] = [rows[k - 1], rows[k], rows[k + 1]], d1 = (b[0] - a[0]) / 1000, d2 = (c[0] - b[0]) / 1000;
  if (d1 <= 0 || d2 <= 0 || b[3] < 3) continue;
  const acc = f => Math.hypot(...[0, 1, 2].map(i => ((f(c, i) - f(b, i)) / d2 - (f(b, i) - f(a, i)) / d1) / ((d1 + d2) / 2)));
  world.push(acc((r, i) => r[5 + i])); relative.push(acc((r, i) => r[5 + i] - r[8 + i]));
  yawRate.push([b[1], Math.atan2(Math.sin(c[2] - b[2]), Math.cos(c[2] - b[2])) / d2]);
}
const turning = s => yawRate.filter(([steer]) => steer === s).map(([, w]) => w);
const result = { label, taken: taken.id, frames: rows.length, fallbackFrames: rows.filter(r => !r[4]).length,
  worldP50: pct(world, 0.5), worldP99: pct(world, 0.99), worldOver100: world.filter(a => a > 100).length,
  relativeP99: pct(relative, 0.99), relativeOver100: relative.filter(a => a > 100).length,
  yawRateRight: pct(turning(1), 0.5), yawRateLeft: pct(turning(-1), 0.5), yawRateStraightP99: pct(turning(0).map(Math.abs), 0.99) };
const dir = path.join(os.homedir(), 'turku3d/loops/carjitter/runs');
fs.mkdirSync(dir, { recursive: true });
fs.writeFileSync(path.join(dir, `${label}.json`), JSON.stringify({ result, rows }));
console.log(JSON.stringify(result));
