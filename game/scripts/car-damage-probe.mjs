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
const url = process.env.PROBE_URL ?? 'https://city.lab.madekivi.fi/?dev=1&time=13:00&weather=clear&traffic=0&walkers=0';
fs.mkdirSync(out, { recursive: true });
const report = { mode, url, errors: [], shots: [], steps: [] }, t0 = Date.now();
const log = (step, data = {}) => { const entry = { t: +((Date.now() - t0) / 1000).toFixed(1), step, ...data }; report.steps.push(entry); console.error(JSON.stringify(entry)); };
const browser = await chromium.connectOverCDP(process.env.BROWSER_CDP, { timeout: 30000 });
const context = browser.contexts()[0] ?? await browser.newContext();
const VIEWS = [['front-left', 35], ['front-right', -35], ['left', 90], ['rear', 180]];

async function open(name) {
  const page = await context.newPage();
  await page.setViewportSize({ width: 960, height: 540 });
  await page.addInitScript(() => Object.defineProperty(navigator, 'getGamepads', { value: () => [] }));
  await (await context.newCDPSession(page)).send('Emulation.setFocusEmulationEnabled', { enabled: true });
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

/** A square building wall with a flat, clear run-up near the starter car: rays at bumper height in 5-degree steps
 *  from candidate start points, the wall face square to the ray (and as far on two parallel rays 0.9 m to each side),
 *  the ground along the way within 0.5 m of the start. Returns the start, heading (chassis yaw) and distance. */
const findWall = page => page.evaluate(async () => {
  const g = window.__game, physics = g.physics, car = physics.latest.vehicles.find(v => v.id === 'player');
  const ray = (o, d, max, filter = 'solid') => physics.raycast(o, d, max, filter);
  let best = null;
  for (const [ox, oz] of [[0, 0], [12, 0], [-12, 0], [0, 12], [0, -12], [20, 20], [-20, -20], [20, -20], [-20, 20]]) {
    const top = { x: car.pos.x + ox, y: car.pos.y + 30, z: car.pos.z + oz };
    const ground = await ray(top, { x: 0, y: -1, z: 0 }, 60);
    if (!ground || Math.abs(ground.point.y - car.pos.y) > 3) continue;
    const y = ground.point.y + .55;
    for (let deg = 0; deg < 360; deg += 5) {
      const yaw = deg * Math.PI / 180, d = { x: Math.cos(yaw), y: 0, z: -Math.sin(yaw) }, side = { x: -d.z, y: 0, z: d.x };
      const o = { x: ground.point.x, y, z: ground.point.z };
      const hit = await ray(o, d, 130);
      if (!hit || hit.toi < 45 || -(hit.normal.x * d.x + hit.normal.z * d.z) < .97 || Math.abs(hit.normal.y) > .2) continue;
      if (best && hit.toi <= best.distance) continue;
      let clear = true;
      for (const k of [-.9, .9]) {
        const h = await ray({ x: o.x + side.x * k, y, z: o.z + side.z * k }, d, 140);
        if (!h || Math.abs(h.toi - hit.toi) > 1) { clear = false; break; }
      }
      for (let t = 4; clear && t < hit.toi - 2; t += 4) {
        const at = await ray({ x: o.x + d.x * t, y: y + 3, z: o.z + d.z * t }, { x: 0, y: -1, z: 0 }, 8);
        if (!at || Math.abs(at.point.y - ground.point.y) > .5) clear = false;
      }
      if (clear) best = { x: o.x, y: ground.point.y, z: o.z, yaw, deg, distance: hit.toi, wall: hit.point, tile: hit.tile };
    }
    if (best && best.distance > 70) break;
  }
  return best;
});

/** One run: the car reset to the start, then W held until `kmh` (released and pressed again to hold it), coasting the
 *  last metres into the wall. Every frame's forward speed is logged in the page; the impact speed is the last speed
 *  before the drop. */
async function drive(page, wall, kmh) {
  await page.evaluate(w => { const p = window.__game.physics; p.resetVehicle({ x: w.x, y: w.y + .75, z: w.z }, w.yaw, 'player'); }, wall);
  await page.waitForTimeout(1500);
  await page.evaluate(() => {
    const log = window.__probeLog = [];
    const tick = () => { const v = window.__game.physics.latest?.vehicles.find(x => x.id === 'player');
      if (v) log.push({ t: performance.now(), v: v.forwardSpeed, x: v.pos.x, z: v.pos.z });
      if (log.length < 2400) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  });
  const target = kmh / 3.6, start = Date.now();
  let held = false;
  const set = async on => { if (on !== held) { held = on; await (on ? page.keyboard.down('KeyW') : page.keyboard.up('KeyW')); } };
  while (Date.now() - start < 25000) {
    const s = await page.evaluate(w => { const v = window.__game.physics.latest.vehicles.find(x => x.id === 'player');
      return { v: v.forwardSpeed, left: w.distance - Math.hypot(v.pos.x - w.x, v.pos.z - w.z) - 2.2 }; }, wall);
    if (s.left < .3 || (s.v < 1 && Date.now() - start > 4000)) break;
    await set(s.left > 4 && s.v < target - .15);
    await page.waitForTimeout(20);
  }
  await set(false);
  await page.waitForTimeout(2000);
  const log = await page.evaluate(() => window.__probeLog);
  let impact = 0;
  for (let i = 1; i < log.length; i++) if (log[i - 1].v - log[i].v > 2) { impact = Math.max(...log.slice(Math.max(0, i - 4), i).map(e => e.v)); break; }
  return { target: kmh, impactKmh: +(impact * 3.6).toFixed(1), frames: log.length };
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
  if (mode === 'wall') {
    await page.evaluate(() => window.__game.enterVehicle('player'));
    await page.waitForTimeout(1500);
    const wall = report.wall = await findWall(page);
    log('wall', wall ?? {});
    if (!wall) throw new Error('no square wall with a clear run-up near the start');
    report.runs = [];
    for (const [label, speeds] of [['30', [30]], ['50', [50]], ['80', [80]], ['60x3', [60, 60, 60]]]) {
      await page.evaluate(() => window.__game.damage.clear());
      await page.waitForTimeout(300);
      for (const kmh of speeds) {
        const run = await drive(page, wall, kmh);
        Object.assign(run, await bounds(page));
        report.runs.push({ label, ...run });
        log(`run ${label}`, { impact: run.impactKmh, front: run.bounds?.frontLoss, hits: run.state?.hits });
      }
      await page.evaluate(w => window.__game.physics.resetVehicle({ x: w.x, y: w.y + .75, z: w.z }, w.yaw, 'player'), wall);
      await page.waitForTimeout(1200);
      await shots(page, `wall${label}`);
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
