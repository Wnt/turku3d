// Car body damage on the deployed share (vehicle/README.md "Body damage"): the starter car from four views, fresh and
// after hits, with the crumple measured from the drawn body's deformed bounds. Drives a Chrome that session.py holds:
//   ~/turku3d/tools/host-lease/session.py run air car-damage --kind func --headless --load-timeout 180 \
//     -- node game/scripts/car-damage-probe.mjs dent OUT_DIR
// Modes: `dent` (developer dents, slice A), `wall` (drives into a wall, slice B), `mp` (a second client sees the damage),
// `perf` (the frame-cost bench), `handling` (mechanical damage: acceleration, speed and pull on a straight, fresh and
// after real 50 and 75 km/h wall crashes, with Wreckfest 2's reverse pull probe), `mpsmoke` (a second client sees the
// engine-bay steam and smoke and the deformed car; the frame cost of the smoke and the damage systems, A/B).
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
  const cars = g.damage.remotes().map(r => r.position);
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
      for (const r of cars) {
        const along = (r[0] - o.x) * d.x + (r[2] - o.z) * d.z, across = (r[0] - o.x) * side.x + (r[2] - o.z) * side.z;
        if (Math.abs(across) < 3 && along > -6 && along < hit.toi + 3) clear = false;
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

/** A flat, clear straight near the start car for handling runs: from candidate start points, rays at bumper height in
 *  5-degree steps (and parallel ones 0.9 m to each side) find the longest corridor without a solid hit, then ground
 *  rays every 4 m along it (centre and both sides) must stay level (within 10 cm step to step, a 2.5 % grade change, and 1.5 m in all) and flat
 *  (normal y > 0.99). The longest usable corridor wins, up to 230 m. */
const findStraight = page => page.evaluate(async () => {
  const physics = window.__game.physics, car = physics.latest.vehicles.find(v => v.id === 'player');
  const cars = window.__game.damage.remotes().map(r => r.position);
  const ray = (o, d, max) => physics.raycast(o, d, max, 'solid');
  let best = null;
  const starts = [[0, 0]];
  for (const r of [25, 50, 80]) for (let a = 0; a < 8; a++) starts.push([r * Math.cos(a * Math.PI / 4), r * Math.sin(a * Math.PI / 4)]);
  for (const [ox, oz] of starts) {
    const ground = await ray({ x: car.pos.x + ox, y: car.pos.y + 30, z: car.pos.z + oz }, { x: 0, y: -1, z: 0 }, 60);
    if (!ground || Math.abs(ground.point.y - car.pos.y) > 6 || ground.normal.y < .995) continue;
    const y = ground.point.y + .55;
    for (let deg = 0; deg < 360; deg += 5) {
      const yaw = deg * Math.PI / 180, d = { x: Math.cos(yaw), y: 0, z: -Math.sin(yaw) }, side = { x: -d.z, y: 0, z: d.x };
      let clear = 230;
      for (const k of [0, -.9, .9]) {
        const h = await ray({ x: ground.point.x + side.x * k, y, z: ground.point.z + side.z * k }, d, 230);
        if (h) clear = Math.min(clear, h.toi);
        if (clear <= (best?.length ?? 120)) break;
      }
      if (clear <= (best?.length ?? 120)) continue;
      let usable = 0, last = [ground.point.y, ground.point.y, ground.point.y];
      for (let t = 4; t < clear - 3; t += 4) {
        let ok = true;
        for (const [j, k] of [[0, 0], [1, -.85], [2, .85]]) {
          const at = await ray({ x: ground.point.x + d.x * t + side.x * k, y: ground.point.y + 4, z: ground.point.z + d.z * t + side.z * k }, { x: 0, y: -1, z: 0 }, 9);
          if (!at || at.normal.y < .99 || Math.abs(at.point.y - last[j]) > .1 || Math.abs(at.point.y - ground.point.y) > 1.5) { ok = false; break; }
          last[j] = at.point.y;
        }
        if (!ok) break;
        usable = t;
      }
      // Other players' cars (parked shared records too) are not in the 'solid' rays: each blocks 7 m either side of it,
      // and the run uses the longest free stretch (its start moves past a car parked at the corridor's beginning).
      const blocked = cars.map(r => [(r[0] - ground.point.x) * d.x + (r[2] - ground.point.z) * d.z, (r[0] - ground.point.x) * side.x + (r[2] - ground.point.z) * side.z])
        .filter(([along, across]) => Math.abs(across) < 3 && along > -7 && along < usable + 7).map(([along]) => along).sort((a, b) => a - b);
      let from = 0, stretch = [0, 0];
      for (const at of [...blocked, usable + 7]) { if (at - 7 - from > stretch[1] - stretch[0]) stretch = [from, at - 7]; from = Math.max(from, at + 7); }
      const length = stretch[1] - stretch[0];
      if (length > (best?.length ?? 120))
        best = { x: ground.point.x + d.x * stretch[0], y: ground.point.y, z: ground.point.z + d.z * stretch[0], yaw, deg, length, skipped: +stretch[0].toFixed(1) };
    }
  }
  return best;
});

/** One driven programme on the straight `s` through the game's own controller hook (`input.driver`, the autopilot's
 *  way in), from a reset at `at` metres along it: 'accel' (full throttle from rest holding the line, then a full stop
 *  before the end), 'pull' (holding the line up to 60 km/h and 2 s more, then 3 s hands off at that speed, then a stop) or
 *  'reverse' (Wreckfest 2's probe: from rest, 2.5 s of straight full reverse hands off, then a stop). The line holder
 *  steers on the heading error, its rate and the sideways offset. The game's own driver (the autopilot) is put back
 *  after. Returns the per-update log. */
const programme = (page, s, kind, at = 0) => page.evaluate(async ([s, kind, at]) => {
  const g = window.__game, car = () => g.physics.latest.vehicles.find(v => v.id === 'player');
  const c = Math.cos(s.yaw), n = Math.sin(s.yaw);
  g.physics.resetVehicle({ x: s.x + c * at, y: s.y + .75, z: s.z - n * at }, s.yaw, 'player');
  await new Promise(r => setTimeout(r, 2500));
  const heading = q => Math.atan2(2 * (q.w * q.y + q.x * q.z), 1 - 2 * (q.y * q.y + q.z * q.z));
  const wrap = a => Math.atan2(Math.sin(a), Math.cos(a));
  const along = v => (v.pos.x - s.x) * c - (v.pos.z - s.z) * n, across = v => -(v.pos.x - s.x) * n - (v.pos.z - s.z) * c;
  return await new Promise(resolve => {
    const log = [], prior = g.input.driver; let t = 0, phase = 'go', since = 0, previous = null;
    g.input.driver = (actions, dt) => {
      const v = car(); if (!v) return;
      t += dt; since += dt;
      const h = wrap(heading(v.rot) - s.yaw), rate = previous === null || dt <= 0 ? 0 : wrap(h - previous) / dt; previous = h;
      log.push({ t, phase, v: v.forwardSpeed, along: along(v), across: across(v), h });
      const hold = Math.max(-1, Math.min(1, 2.5 * h + .6 * rate + .05 * across(v)));
      let throttle = 0, brake = 0, steer = hold;
      const next = p => { phase = p; since = 0; };
      if (kind === 'accel') {
        if (phase === 'go') { throttle = 1; if (along(v) > s.length - 10 - v.forwardSpeed ** 2 / 20) next('stop'); }
      } else if (kind === 'pull') {
        if (phase === 'go') { throttle = 1; if (v.forwardSpeed >= 60 / 3.6) next('settle'); }
        else if (phase === 'settle') { throttle = v.forwardSpeed < 60 / 3.6 ? 1 : .35; if (since >= 2) next('hands'); }
        else if (phase === 'hands') { throttle = v.forwardSpeed < 60 / 3.6 ? 1 : .35; steer = 0; if (since >= 3 || along(v) > s.length - 25) next('stop'); }
      } else if (phase === 'go') { brake = 1; steer = 0; if (since >= 2.5) next('stop'); }
      if (phase === 'stop') {
        steer = kind === 'reverse' ? 0 : hold;
        if (kind === 'reverse') throttle = v.forwardSpeed < -.3 ? 1 : 0; else brake = v.forwardSpeed > .3 ? 1 : 0;
        if (Math.abs(v.forwardSpeed) < .3 && since > .4) phase = 'done';
      }
      if (phase === 'done') { throttle = 0; brake = 0; steer = 0; }
      Object.assign(actions, { throttle, brake, steer, handbrake: 0 });
      if (phase === 'done' || t > 40) { g.input.driver = prior; resolve(log); }
    };
  });
}, [s, kind, at]);

/** The handling numbers on the straight: 0-50 and 0-80 km/h (s), the speed after 100 m and the best before braking
 *  (km/h), the hands-off yaw rate at 60 km/h (degrees/s, + left) and its sideways drift (m), and the reverse probe's
 *  heading change (degrees, + left). */
async function straightRun(page, s) {
  const accel = await programme(page, s, 'accel');
  // Timed from the first update with the throttle open (the car at rest after the reset's settle).
  const moving = 0, t0 = accel[0]?.t ?? 0;
  const at = kmh => { const e = accel.find(e => e.v * 3.6 >= kmh); return e ? +(e.t - t0).toFixed(2) : null; };
  const peak = accel.reduce((m, e, i) => e.v > accel[m].v ? i : m, 0);
  // The hands-off pull after a 0.5 s transient (the line holder's last correction dying out).
  const hands = (await programme(page, s, 'pull')).filter(e => e.phase === 'hands'), pull = hands.filter(e => e.t - hands[0].t >= .5);
  const first = pull[0], last = pull[pull.length - 1];
  const reverse = await programme(page, s, 'reverse', s.length - 4);
  return { t50: at(50), t80: at(80), v100: +((accel.find(e => e.along >= 100)?.v ?? NaN) * 3.6).toFixed(1), vmax: +(accel[peak].v * 3.6).toFixed(1),
    distance: +accel[peak].along.toFixed(1), lineHeldM: +Math.max(...accel.slice(moving, peak).map(e => Math.abs(e.across))).toFixed(2),
    pullDegPerS: first && last && last.t > first.t ? +((last.h - first.h) * 180 / Math.PI / (last.t - first.t)).toFixed(3) : null,
    pullKmh: first ? +(first.v * 3.6).toFixed(1) : null, pullSideways: first ? +(last.across - first.across).toFixed(2) : null,
    reverseDeg: +((reverse[reverse.length - 1].h - reverse[0].h) * 180 / Math.PI).toFixed(3) };
}
const mech = page => page.evaluate(() => window.__game.damage.mechanics());

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
    // The world must be streamed in first: a moving baseline (draws growing) would swamp a sub-millisecond change.
    await page.evaluate(() => window.__game.damage.look({ yaw: 180, pitch: 22, distance: 9, height: .5 }));
    await page.waitForTimeout(25000);
    report.perf = [];
    const configs = [['none', 0], ...Array.from({ length: 4 }, () => [['plain', 20], ['damaged', 20], ['plain', 60], ['damaged', 60]]).flat(), ['none', 0]];
    for (const [kind, count] of configs) {
      const placed = await page.evaluate(([kind, count]) => window.__game.damage.bench({ count, kind }), [kind, count]);
      await page.waitForTimeout(2500);
      await page.evaluate(() => window.__render.perf.reset());
      await page.waitForTimeout(6000);
      const p = await page.evaluate(() => { const s = window.__perf, passes = window.__render.perf.passSnapshot?.();
        return { gpu: s.gpuMs, cpu: s.cpuMs, draws: s.drawCalls, status: s.gpuStatus,
          passes: passes ? Object.fromEntries(Object.entries(passes.gpuMs ?? {}).map(([k, v]) => [k, v?.p50 ?? null])) : null }; });
      const scene = p.passes ? ['prepass', 'shadows', 'beauty'].reduce((s, k) => s + (p.passes[k] ?? 0), 0) : null;
      const row = { kind, count, placed, gpuP50: p.gpu?.p50, gpuP95: p.gpu?.p95, sceneP50: scene, cpuP50: p.cpu?.p50, samples: p.gpu?.samples, draws: p.draws, passes: p.passes };
      report.perf.push(row); log('perf', { ...row, passes: undefined });
    }
    // Medians over the alternating rounds: the damage cost is damaged minus plain at the same count and draws.
    const median = a => { const s = [...a].sort((x, y) => x - y); return s[s.length >> 1]; };
    report.perfSummary = [20, 60].map(count => {
      const of = (kind, key) => median(report.perf.filter(r => r.kind === kind && r.count === count).map(r => r[key]));
      return { count, plainGpu: of('plain', 'gpuP50'), damagedGpu: of('damaged', 'gpuP50'), plainScene: of('plain', 'sceneP50'), damagedScene: of('damaged', 'sceneP50'),
        plainCpu: of('plain', 'cpuP50'), damagedCpu: of('damaged', 'cpuP50') };
    });
    log('perf summary', { summary: report.perfSummary });
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
  if (mode === 'handling') {
    report.home = await page.evaluate(() => { const v = window.__game.physics.latest.vehicles.find(v => v.id === 'player');
      return { pos: { ...v.pos, y: v.pos.y + .2 }, yaw: Math.atan2(2 * (v.rot.w * v.rot.y + v.rot.x * v.rot.z), 1 - 2 * (v.rot.y * v.rot.y + v.rot.z * v.rot.z)) }; });
    // Both searches on foot: a parked car holds its brake, a driven one with no pedal pressed rolls on a slope.
    const wall = report.wall = await findWall(page);
    log('wall', wall ?? {});
    if (!wall) throw new Error('no square wall with a clear run-up near the start');
    const straight = report.straight = await findStraight(page);
    log('straight', straight ?? {});
    if (!straight) throw new Error('no flat clear straight near the start');
    await page.evaluate(() => window.__game.enterVehicle('player'));
    await page.waitForTimeout(1500);
    report.handling = [];
    const measure = async label => {
      for (let i = 0; i < 2; i++) {
        const row = { label, run: i + 1, ...await straightRun(page, straight) };
        report.handling.push(row); log('handling', row);
      }
    };
    await measure('fresh');
    for (const kmh of [50, 75]) {
      await page.evaluate(() => window.__game.damage.clear());
      await page.waitForTimeout(300);
      const crash = await drive(page, wall, kmh);
      Object.assign(crash, await bounds(page), { mechanics: await mech(page) });
      report[`crash${kmh}`] = crash;
      log(`crash ${kmh}`, { impact: crash.impactKmh, front: crash.bounds?.frontLoss, mechanics: crash.mechanics });
      await measure(`after ${kmh}`);
      if (kmh === 75) {
        // The chase view while driving the damaged car: steam from the bonnet and the HUD indicator.
        await page.evaluate(s => window.__game.physics.resetVehicle({ x: s.x, y: s.y + .75, z: s.z }, s.yaw, 'player'), straight);
        await page.waitForTimeout(2500);
        report.smoke75 = await page.evaluate(() => window.__game.damage.smoke());
        const file = path.join(out, 'a-after75-chase.png'); await page.screenshot({ path: file }); report.shots.push(file);
        await shots(page, 'after75');
      }
    }
    const mean = (label, key) => { const r = report.handling.filter(h => h.label === label).map(h => h[key]).filter(Number.isFinite); return r.length ? +(r.reduce((a, b) => a + b, 0) / r.length).toFixed(3) : null; };
    report.summary = ['fresh', 'after 50', 'after 75'].map(label => ({ label, ...Object.fromEntries(['t50', 't80', 'v100', 'vmax', 'pullDegPerS', 'pullSideways', 'reverseDeg'].map(k => [k, mean(label, k)])) }));
    log('summary', { summary: report.summary });
    // The room keeps this client's car after it leaves: park it back where it started, off the test corridors.
    await page.evaluate(p => window.__game.physics.resetVehicle(p.pos, p.yaw, 'player'), report.home);
    await page.waitForTimeout(1500);
  }
  if (mode === 'mpsmoke') {
    await page.evaluate(() => window.__game.enterVehicle('player'));
    await page.waitForTimeout(1500);
    await page.keyboard.down('KeyW'); await page.waitForTimeout(900); await page.keyboard.up('KeyW');
    await page.waitForTimeout(2500);
    // A heavy frontal wreck: radiator 3 and engine 1 (steam and smoke), and a crushed nose.
    await page.evaluate(() => { for (let i = 0; i < 4; i++) window.__game.damage.dent({ kmh: 78, at: 'front' }); });
    await page.waitForTimeout(800);
    report.a = { ...await bounds(page), mechanics: await mech(page), smoke: await page.evaluate(() => window.__game.damage.smoke()) };
    log('a wrecked', { mechanics: report.a.mechanics, smoke: report.a.smoke, front: report.a.bounds?.frontLoss });
    let file = path.join(out, 'a-wrecked-chase.png'); await page.screenshot({ path: file }); report.shots.push(file);
    await shots(page, 'wrecked', 'a');
    const b = await open('b', await browser.newContext());
    const started = Date.now();
    await b.waitForFunction(hits => window.__game.damage.remotes().some(r => r.hits === hits), report.a.state.hits, { timeout: 90000 });
    report.replicaSeconds = (Date.now() - started) / 1000;
    await b.evaluate(() => window.__game.damage.look({ yaw: 30, pitch: 16, distance: 7, remote: true }));
    await b.waitForTimeout(2500);
    report.b = { remotes: await b.evaluate(() => window.__game.damage.remotes()), smoke: await b.evaluate(() => window.__game.damage.smoke()) };
    log('b sees a', report.b);
    file = path.join(out, 'b-remote-smoke.png'); await b.screenshot({ path: file }); report.shots.push(file);
    await shots(b, 'wrecked', 'b', true);
    const theirs = report.b.remotes.find(r => r.hits === report.a.state.hits);
    if (!theirs || Math.abs(theirs.frontLoss - report.a.bounds.frontLoss) > .2) report.errors.push(`replica shape differs: own ${report.a.bounds.frontLoss} cm, remote ${theirs?.frontLoss}`);
    if (!theirs || theirs.mechanics.cooling !== report.a.mechanics.cooling || theirs.mechanics.engine !== report.a.mechanics.engine) report.errors.push('replica mechanics differ');
    if (!(report.b.smoke?.steam > 0) || !(report.b.smoke?.smoke > 0)) report.errors.push(`no remote steam/smoke on b: ${JSON.stringify(report.b.smoke)}`);
    await b.close();
    // Frame cost on A, chase view, the car standing with its engine running: wrecked (steam, smoke, cage) against a
    // fresh body, alternating rounds of 6 s after a 2 s settle; GPU and CPU frame time p50.
    await page.evaluate(() => window.__game.damage.look({ yaw: 180, pitch: 12, distance: 6.5, height: .4 }));
    const wire = await page.evaluate(() => window.__game.physics.latest.vehicles.find(v => v.id === 'player').damage);
    report.cost = [];
    for (let round = 0; round < 3; round++) for (const kind of ['fresh', 'wrecked']) {
      await page.evaluate(([kind, wire]) => window.__game.physics.setVehicleDamage('player', kind === 'fresh' ? null : wire), [kind, wire]);
      await page.waitForTimeout(2000);
      await page.evaluate(() => window.__render.perf.reset());
      await page.waitForTimeout(6000);
      const p = await page.evaluate(() => ({ gpu: window.__perf.gpuMs, cpu: window.__perf.cpuMs, draws: window.__perf.drawCalls, smoke: window.__game.damage.smoke() }));
      const row = { kind, round, gpuP50: p.gpu?.p50, cpuP50: p.cpu?.p50, draws: p.draws, smoke: p.smoke };
      report.cost.push(row); log('cost', row);
    }
    const median = a => { const v = a.filter(Number.isFinite).sort((x, y) => x - y); return v.length ? v[v.length >> 1] : null; };
    report.costSummary = Object.fromEntries(['fresh', 'wrecked'].map(kind => [kind, { gpuP50: median(report.cost.filter(r => r.kind === kind).map(r => r.gpuP50)),
      cpuP50: median(report.cost.filter(r => r.kind === kind).map(r => r.cpuP50)) }]));
    log('cost summary', report.costSummary);
    await page.evaluate(() => window.__game.damage.look(null));
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
