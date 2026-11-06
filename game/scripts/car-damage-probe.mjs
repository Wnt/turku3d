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

async function open(name, own = context) {
  const page = await own.newPage();
  await page.setViewportSize({ width: 960, height: 540 });
  await page.addInitScript(() => Object.defineProperty(navigator, 'getGamepads', { value: () => [] }));
  await (await own.newCDPSession(page)).send('Emulation.setFocusEmulationEnabled', { enabled: true });
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

async function shots(page, label, client = 'a', remote = false) {
  for (const [view, yaw] of VIEWS) {
    await page.evaluate(([yaw, remote]) => window.__game.damage.look({ yaw, pitch: 14, distance: 6.2, remote }), [yaw, remote]);
    await settle(page, 12);
    const file = path.join(out, `${client}-${label}-${view}.png`);
    await page.screenshot({ path: file });
    report.shots.push(file);
  }
  await page.evaluate(() => window.__game.damage.look(null));
}

/** A square building wall with a flat, clear run-up near the starter car: rays at bumper height in 5-degree steps
 *  from candidate start points; a hit wall's own normal then sets the heading (the car square to it), and along that
 *  heading the wall must be flat across the car (two parallel rays 0.85 m to each side within 7 cm) and the ground
 *  within 0.5 m of the start. The longest such run-up wins (up to 125 m, enough for 80 km/h). */
const findWall = page => page.evaluate(async () => {
  const g = window.__game, physics = g.physics, car = physics.latest.vehicles.find(v => v.id === 'player');
  const ray = (o, d, max, filter = 'solid') => physics.raycast(o, d, max, filter);
  let best = null;
  const starts = [[0, 0]];
  for (const r of [15, 30, 45]) for (let a = 0; a < 8; a++) starts.push([r * Math.cos(a * Math.PI / 4), r * Math.sin(a * Math.PI / 4)]);
  for (const [ox, oz] of starts) {
    const ground = await ray({ x: car.pos.x + ox, y: car.pos.y + 30, z: car.pos.z + oz }, { x: 0, y: -1, z: 0 }, 60);
    if (!ground || Math.abs(ground.point.y - car.pos.y) > 4 || ground.normal.y < .99) continue;
    const y = ground.point.y + .55, o = { x: ground.point.x, y, z: ground.point.z };
    for (let deg = 0; deg < 360; deg += 5) {
      const yaw0 = deg * Math.PI / 180, first = await ray(o, { x: Math.cos(yaw0), y: 0, z: -Math.sin(yaw0) }, 130);
      if (!first || first.toi < 50 || Math.abs(first.normal.y) > .1) continue;
      const n = Math.hypot(first.normal.x, first.normal.z), d = { x: -first.normal.x / n, y: 0, z: -first.normal.z / n };
      const yaw = Math.atan2(-d.z, d.x), side = { x: -d.z, y: 0, z: d.x };
      const hit = await ray(o, d, 130);
      if (!hit || hit.toi < 50 || hit.toi > 125 || (best && hit.toi <= best.distance) || -(hit.normal.x * d.x + hit.normal.z * d.z) < .998) continue;
      let clear = true;
      for (const k of [-.85, .85]) {
        const h = await ray({ x: o.x + side.x * k, y, z: o.z + side.z * k }, d, 140);
        if (!h || Math.abs(h.toi - hit.toi) > .07) { clear = false; break; }
      }
      for (let t = 4; clear && t < hit.toi - 2; t += 4) for (const k of [-.85, 0, .85]) {
        const at = await ray({ x: o.x + d.x * t + side.x * k, y: y + 3, z: o.z + d.z * t + side.z * k }, { x: 0, y: -1, z: 0 }, 8);
        if (!at || Math.abs(at.point.y - ground.point.y) > .5) { clear = false; break; }
      }
      if (clear) best = { x: o.x, y: ground.point.y, z: o.z, yaw, deg: Math.round(yaw * 180 / Math.PI), distance: hit.toi, wall: hit.point, tile: hit.tile };
    }
    if (best && best.distance > 80) break;
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
  if (mode === 'perf') {
    // GPU frame time (window.__perf, timestamp queries) with N extra starter cars in view: damaged, damage-capable but
    // undamaged, and the plain fleet material without the cage. A fixed camera; 5 s settle, then a fresh 10 s window.
    await page.evaluate(() => window.__game.damage.look({ yaw: 180, pitch: 22, distance: 9, height: .5 }));
    report.perf = [];
    for (const [kind, count] of [['none', 0], ['plain', 20], ['capable', 20], ['damaged', 20], ['plain', 60], ['damaged', 60], ['none', 0]]) {
      const placed = await page.evaluate(([kind, count]) => window.__game.damage.bench({ count, kind }), [kind, count]);
      await page.waitForTimeout(5000);
      await page.evaluate(() => window.__render.perf.reset());
      await page.waitForTimeout(10000);
      const p = await page.evaluate(() => { const s = window.__perf; return { gpu: s.gpuMs, cpu: s.cpuMs, frame: s.frameMs, draws: s.drawCalls, status: s.gpuStatus }; });
      const row = { kind, count, placed, gpuP50: p.gpu?.p50, gpuP95: p.gpu?.p95, cpuP50: p.cpu?.p50, frameP50: p.frame?.p50, samples: p.gpu?.samples, draws: p.draws, status: p.status };
      report.perf.push(row); log('perf', row);
    }
    await page.evaluate(() => window.__game.damage.bench({ count: 0 }));
    await shots(page, 'perf');
  }
  if (mode === 'mp') {
    // Client A drives its starter car a little (it registers as a shared record), client B joins the same room and
    // must draw A's dents exactly: hits and the crumple from B's own deformed bounds.
    await page.evaluate(() => window.__game.enterVehicle('player'));
    await page.waitForTimeout(1500);
    await page.keyboard.down('KeyW'); await page.waitForTimeout(900); await page.keyboard.up('KeyW');
    await page.waitForTimeout(2500);
    // Its own browser context: its own session token, so the room sees a second player.
    const b = await open('b', await browser.newContext());
    await b.waitForFunction(() => window.__game.damage.remotes().length > 0, null, { timeout: 90000 });
    report.bFresh = await b.evaluate(() => window.__game.damage.remotes());
    log('b sees a', { remotes: report.bFresh });
    await shots(b, 'fresh', 'b', true);
    await page.evaluate(() => { window.__game.damage.dent({ kmh: 80, at: 'front' }); window.__game.damage.dent({ kmh: 55, at: 'rear-left' }); });
    await page.waitForTimeout(500);
    report.a = await bounds(page);
    const started = Date.now();
    await b.waitForFunction(hits => window.__game.damage.remotes().some(r => r.hits === hits), report.a.state.hits, { timeout: 30000 });
    report.replicaSeconds = (Date.now() - started) / 1000;
    report.b = await b.evaluate(() => window.__game.damage.remotes());
    log('replicated', { a: { hits: report.a.state.hits, front: report.a.bounds.frontLoss, rear: report.a.bounds.rearLoss }, b: report.b, seconds: report.replicaSeconds });
    await shots(page, 'dented', 'a');
    await shots(b, 'dented', 'b', true);
    const mine = report.a.bounds, theirs = report.b.find(r => r.hits === report.a.state.hits);
    if (!theirs || Math.abs(theirs.frontLoss - mine.frontLoss) > .2 || Math.abs(theirs.rearLoss - mine.rearLoss) > .2)
      report.errors.push(`replica differs: own ${mine.frontLoss}/${mine.rearLoss} cm, remote ${theirs?.frontLoss}/${theirs?.rearLoss} cm`);
    await b.close();
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
