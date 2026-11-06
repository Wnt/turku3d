// Seated drivers on the deployed share (game/src/actors/driver/README.md): the starter car's driver from the chase and
// side views at full lock left and right, under hard braking and in a wall crash, a second client's view of it, and the
// frame cost of 20 animated drivers. Drives a Chrome that session.py holds:
//   ~/turku3d/tools/host-lease/session.py run air driver-rig --kind func --headless --load-timeout 180 \
//     -- node game/scripts/driver-probe.mjs shots OUT_DIR
//   ~/turku3d/tools/host-lease/session.py run air driver-rig --kind perf --headless --load-timeout 180 \
//     -- node game/scripts/driver-probe.mjs perf OUT_DIR
// Each view is shot as drawn and with the fleet glazing hidden (`-noglass`), to judge the posture behind the glass.
// Writes OUT_DIR/result.json and PNGs; prints one JSON line.
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';

// Modes (comma-separated): shots (rest, lock both ways, horn), brake, crash, remote (a second client), perf, ambient
// (traffic drivers on against off; set PROBE_URL with traffic on).
const mode = process.argv[2] ?? 'shots,brake,crash,remote', modes = new Set(mode.split(',')), out = path.resolve(process.argv[3] ?? 'driver-probe');
const url = process.env.PROBE_URL ?? 'https://city.lab.madekivi.fi/?dev=1&time=13:00&weather=clear&traffic=0&walkers=0';
fs.mkdirSync(out, { recursive: true });
const report = { mode, url, errors: [], shots: [], steps: [] }, t0 = Date.now();
const log = (step, data = {}) => { const entry = { t: +((Date.now() - t0) / 1000).toFixed(1), step, ...data }; report.steps.push(entry); console.error(JSON.stringify(entry)); };
const browser = await chromium.connectOverCDP(process.env.BROWSER_CDP, { timeout: 30000 });
const context = browser.contexts()[0] ?? await browser.newContext();

async function open(name, own = context) {
  // REUSE=1: an already open game page of the session (a warm page skips the cold load).
  const warm = process.env.REUSE && own === context ? own.pages().find(p => p.url().includes('city.lab')) : null;
  const page = warm ?? await own.newPage();
  if (warm) {
    await page.waitForFunction(() => window.__game?.ready, null, { timeout: 240000 });
    if (await page.evaluate(() => { const s = window.__game.state?.(); return !!s && !s.startScreen && !s.loading; })) { log(`${name} playing (reused)`); return page; }
  }
  await page.setViewportSize({ width: 960, height: 540 });
  await page.addInitScript(() => Object.defineProperty(navigator, 'getGamepads', { value: () => [] }));
  await (await own.newCDPSession(page)).send('Emulation.setFocusEmulationEnabled', { enabled: true });
  page.on('pageerror', e => report.errors.push(`${name} pageerror: ${e.message}`.slice(0, 300)));
  page.on('console', m => { const t = m.text();
    if (/GPUValidationError|Invalid (ShaderModule|RenderPipeline|BindGroup)|WGSL|TypeError|ReferenceError/.test(t)) report.errors.push(`${name} console: ${t}`.slice(0, 300)); });
  if (!warm) await page.goto(`${url}&probe=${Date.now()}`, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await page.waitForFunction(() => window.__game?.ready, null, { timeout: 240000 });
  await page.getByText('Multiplayer', { exact: true }).click();
  await page.waitForFunction(() => { const s = window.__game.state?.(); return s && !s.startScreen && !s.loading && window.__game.damage?.bounds(); }, null, { timeout: 180000 });
  log(`${name} playing`, { release: await page.evaluate(() => fetch('/version.json').then(r => r.json()).then(v => v.sha).catch(() => null)) });
  return page;
}

const settle = (page, frames = 20) => page.evaluate(n => new Promise(resolve => {
  let left = n; const tick = () => (--left <= 0 ? resolve() : requestAnimationFrame(tick)); requestAnimationFrame(tick);
}), frames);

/** Waits (up to `ms`) until the page renders at least `min` frames per second over one second: a shared host's slow
 *  phases (first-use builds, another session's load) feed the physics worker too seldom for scripted driving. */
const waitFps = (page, min = 15, ms = 120000) => page.evaluate(async ([min, ms]) => {
  const t0 = performance.now();
  for (;;) {
    const f0 = performance.now(); let n = 0;
    await new Promise(r => { const t = () => { n++; if (performance.now() - f0 < 1000) requestAnimationFrame(t); else r(); }; requestAnimationFrame(t); });
    if (n >= min || performance.now() - t0 > ms) return n;
  }
}, [min, ms]);

/** The local driver's snapshot entry (hands' distance from the grips, motion channels). */
const driver = (page, key = 'local') => page.evaluate(key => window.__game.drivers.snapshot().drivers.find(d => d.key === key) ?? null, key);
/** Shows or hides the fleet glazing (only the cars' own glass materials). */
const glass = (page, visible) => page.evaluate(visible => {
  const d = [...window.__game.drivers.drivers.values()][0];
  let root = d?.frame; while (root?.parent) root = root.parent;
  root?.traverse(o => { if (o.isMesh && /Finnish fleet.*glass/i.test(o.material?.name ?? '')) o.visible = visible; });
}, visible);

/** Holds the drive input through the game's own controller hook (input.driver); null hands it back. */
const hold = (page, input) => page.evaluate(input => {
  const g = window.__game;
  g.input.driver = input ? actions => { Object.assign(actions, { throttle: 0, brake: 0, steer: 0, handbrake: 0, ...input }); } : null;
}, input);

async function shot(page, label, view, both = true) {
  await page.evaluate(view => window.__game.damage.look(view), view);
  await settle(page, 10);
  for (const visible of both ? [true, false] : [true]) {
    if (both) { await glass(page, visible); await settle(page, 3); }
    const file = path.join(out, `${label}${visible ? '' : '-noglass'}.png`);
    await page.screenshot({ path: file });
    report.shots.push(file);
  }
  if (both) await glass(page, true);
}

const CHASE = { yaw: 180, pitch: 13, distance: 6.2, height: 0.6 }, SIDE = { yaw: 90, pitch: 8, distance: 4.2, height: 0.45 };
const FRONT = { yaw: 28, pitch: 9, distance: 4.4, height: 0.5 };

/** A flat, clear straight near the start car (car-damage-probe.mjs findStraight, shortened to 80 m). */
const findStraight = page => page.evaluate(async () => {
  const physics = window.__game.physics, car = physics.latest.vehicles.find(v => v.id === 'player');
  const ray = (o, d, max) => physics.raycast(o, d, max, 'solid');
  let best = null;
  const starts = [[0, 0]];
  for (const r of [25, 50]) for (let a = 0; a < 8; a++) starts.push([r * Math.cos(a * Math.PI / 4), r * Math.sin(a * Math.PI / 4)]);
  for (const [ox, oz] of starts) {
    const ground = await ray({ x: car.pos.x + ox, y: car.pos.y + 30, z: car.pos.z + oz }, { x: 0, y: -1, z: 0 }, 60);
    if (!ground || Math.abs(ground.point.y - car.pos.y) > 6 || ground.normal.y < .995) continue;
    const y = ground.point.y + .55;
    for (let deg = 0; deg < 360; deg += 10) {
      const yaw = deg * Math.PI / 180, d = { x: Math.cos(yaw), y: 0, z: -Math.sin(yaw) }, side = { x: -d.z, y: 0, z: d.x };
      let clear = 160;
      for (const k of [0, -.9, .9]) {
        const h = await ray({ x: ground.point.x + side.x * k, y, z: ground.point.z + side.z * k }, d, 160);
        if (h) clear = Math.min(clear, h.toi);
      }
      if (clear <= (best?.length ?? 80)) continue;
      let ok = true;
      for (let t = 4; ok && t < clear - 3; t += 6) {
        const at = await ray({ x: ground.point.x + d.x * t, y: ground.point.y + 4, z: ground.point.z + d.z * t }, { x: 0, y: -1, z: 0 }, 9);
        if (!at || at.normal.y < .99 || Math.abs(at.point.y - ground.point.y) > 1.2) ok = false;
      }
      if (ok) best = { x: ground.point.x, y: ground.point.y, z: ground.point.z, yaw, deg, length: clear };
    }
    if (best && best.length > 120) break;
  }
  return best;
});

/** A square wall with a clear run-up (car-damage-probe.mjs findWall). */
const findWall = page => page.evaluate(async () => {
  const physics = window.__game.physics, car = physics.latest.vehicles.find(v => v.id === 'player');
  const ray = (o, d, max) => physics.raycast(o, d, max, 'solid');
  let best = null;
  const starts = [[0, 0]];
  for (const r of [15, 30, 45]) for (let a = 0; a < 8; a++) starts.push([r * Math.cos(a * Math.PI / 4), r * Math.sin(a * Math.PI / 4)]);
  for (const [ox, oz] of starts) {
    const ground = await ray({ x: car.pos.x + ox, y: car.pos.y + 30, z: car.pos.z + oz }, { x: 0, y: -1, z: 0 }, 60);
    if (!ground || Math.abs(ground.point.y - car.pos.y) > 4 || ground.normal.y < .99) continue;
    const y = ground.point.y + .55, o = { x: ground.point.x, y, z: ground.point.z };
    for (let deg = 0; deg < 360; deg += 5) {
      const yaw0 = deg * Math.PI / 180, first = await ray(o, { x: Math.cos(yaw0), y: 0, z: -Math.sin(yaw0) }, 90);
      if (!first || first.toi < 35 || Math.abs(first.normal.y) > .1) continue;
      const n = Math.hypot(first.normal.x, first.normal.z), d = { x: -first.normal.x / n, y: 0, z: -first.normal.z / n };
      const yaw = Math.atan2(-d.z, d.x), side = { x: -d.z, y: 0, z: d.x };
      const hit = await ray(o, d, 90);
      if (!hit || hit.toi < 35 || (best && hit.toi <= best.distance) || -(hit.normal.x * d.x + hit.normal.z * d.z) < .995) continue;
      let clear = true;
      for (const k of [-.85, .85]) { const h = await ray({ x: o.x + side.x * k, y, z: o.z + side.z * k }, d, 100); if (!h || Math.abs(h.toi - hit.toi) > .1) clear = false; }
      for (let t = 4; clear && t < hit.toi - 2; t += 5) {
        const at = await ray({ x: o.x + d.x * t, y: y + 3, z: o.z + d.z * t }, { x: 0, y: -1, z: 0 }, 8);
        if (!at || Math.abs(at.point.y - ground.point.y) > .5) clear = false;
      }
      if (clear) best = { x: o.x, y: ground.point.y, z: o.z, yaw, distance: hit.toi };
    }
    if (best && best.distance > 60) break;
  }
  return best;
});

/** Drives the line `s` from its start up to `kmh` holding the heading, then `then` ('brake': full brakes; 'coast':
 *  throttle held into whatever is ahead, a wall). Resolves when `until(driver pose)` first holds (or after 20 s), with
 *  the input left as it was then. */
const run = (page, s, kmh, then) => page.evaluate(async ([s, kmh, then]) => {
  const g = window.__game, car = () => g.physics.latest.vehicles.find(v => v.id === 'player');
  g.physics.resetVehicle({ x: s.x, y: s.y + .75, z: s.z }, s.yaw, 'player');
  await new Promise(r => setTimeout(r, 2500));
  const heading = q => Math.atan2(2 * (q.w * q.y + q.x * q.z), 1 - 2 * (q.y * q.y + q.z * q.z));
  const wrap = a => Math.atan2(Math.sin(a), Math.cos(a));
  return await new Promise(resolve => {
    let t = 0, phase = 'go', previous = null, since = 0, peak = { pitch: 0, headPitch: 0, at: 0 }, speedAt = 0;
    g.input.driver = (actions, dt) => {
      const v = car(); if (!v) return;
      t += dt; since += dt;
      const h = wrap(heading(v.rot) - s.yaw), rate = previous === null || dt <= 0 ? 0 : wrap(h - previous) / dt; previous = h;
      const steer = Math.max(-1, Math.min(1, .8 * h + .25 * rate));
      const pose = g.drivers.snapshot().drivers.find(d => d.key === 'local')?.pose;
      if (pose && phase !== 'go') { if (pose.pitch > peak.pitch) peak = { ...peak, pitch: pose.pitch, at: since }; peak.headPitch = Math.max(peak.headPitch, pose.headPitch); }
      if (phase === 'go') {
        Object.assign(actions, { throttle: 1, brake: 0, steer, handbrake: 0 });
        if (v.forwardSpeed >= kmh / 3.6) { phase = then; since = 0; speedAt = v.forwardSpeed; }
      } else if (phase === 'brake') Object.assign(actions, { throttle: 0, brake: 1, steer, handbrake: 0 });
      else Object.assign(actions, { throttle: .6, brake: 0, steer, handbrake: 0 });
      const done = phase === 'brake' ? since >= 0.45 : phase === 'coast' ? !!pose && pose.headPitch > 0.25 : false;
      if (done || t > 20) resolve({ phase, t, since, kmh: +(speedAt * 3.6).toFixed(1), now: +(v.forwardSpeed * 3.6).toFixed(1), peak, pose });
    };
  });
}, [s, kmh, then]);

/** Mean and percentile frame times over `n` frames (ms). */
const frames = (page, n = 180) => page.evaluate(n => new Promise(resolve => {
  const times = []; let last = performance.now();
  const tick = () => { const now = performance.now(); times.push(now - last); last = now;
    if (times.length >= n) { times.sort((a, b) => a - b); resolve({ meanMs: +(times.reduce((a, b) => a + b) / n).toFixed(2), p50: +times[n >> 1].toFixed(2), p95: +times[Math.floor(n * .95)].toFixed(2) }); }
    else requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
}), n);

/** Waits (up to `ms`) until the draw count holds within 3 % over 10 s: the world around the bench is still streaming
 *  in after the load, and a growing scene hides the drivers' own cost. */
const settleDraws = (page, ms = 120000) => page.evaluate(async ms => {
  const t0 = performance.now(), samples = [];
  for (;;) {
    await new Promise(r => setTimeout(r, 2000));
    samples.push(window.__perf?.drawCalls ?? 0);
    const last = samples.slice(-5), lo = Math.min(...last), hi = Math.max(...last);
    if (last.length === 5 && hi - lo <= 0.03 * hi) return { draws: hi, seconds: Math.round((performance.now() - t0) / 1000), settled: true };
    if (performance.now() - t0 > ms) return { draws: hi, seconds: Math.round((performance.now() - t0) / 1000), settled: false, samples };
  }
}, ms);

/** Seats (or removes, count 0) a driver in each of the car-damage console's bench cars, animated every frame from a
 *  synthetic drive (each its own steer and speed wave), as 20 nearby driven cars would be. */
const benchDrivers = (page, on) => page.evaluate(on => {
  const g = window.__game, d = g.drivers;
  d.__finish ??= d.finish;
  if (!on) { d.finish = d.__finish; return 0; }
  let root = [...d.drivers.values()][0]?.frame; while (root?.parent) root = root.parent;
  const cars = []; root.traverse(o => { if (o.name.startsWith('bench/')) cars.push(o); });
  const layout = d.drivers.get('local').layout;
  d.finish = (camera, now = performance.now()) => {
    const t = now / 1000;
    cars.forEach((car, i) => {
      const steer = 0.5 * Math.sin(t * 0.9 + i), speed = 8 + 4 * Math.sin(t * 0.7 + i * 1.3);
      const f = car.getWorldDirection(new car.position.constructor());
      d.seat(`bench:${i}`, car, layout, { velocity: { x: f.x * speed, y: 0, z: f.z * speed }, wheels: [{ steer }, { steer }], horn: i % 7 === 0 && Math.sin(t) > 0.8 });
    });
    d.__finish(camera, now);
  };
  return cars.length;
}, on);

try {
  const page = await open('a');
  await page.evaluate(() => window.__game.enterVehicle('player'));
  await page.waitForFunction(() => window.__game.drivers.snapshot().drivers.some(d => d.key === 'local' && d.drawn), null, { timeout: 60000 });
  await page.evaluate(() => window.__game.damage.clear());
  await page.waitForTimeout(3000);
  log('seated', { driver: await driver(page) });
  report.fps = await waitFps(page);
  log('fps', { fps: report.fps });
  // Shots away from the spawn's parked cars: at the start of a clear straight.
  const straight = modes.has('shots') || modes.has('brake') || modes.has('crash') ? await findStraight(page) : null;
  log('straight', { straight });
  if (straight) {
    await page.evaluate(s => window.__game.physics.resetVehicle({ x: s.x, y: s.y + .75, z: s.z }, s.yaw, 'player'), straight);
    await page.waitForTimeout(2500);
  }
  if (modes.has('shots')) {
    report.views = {};
    for (const [name, steer] of [['rest', 0], ['lock-left', -1], ['lock-right', 1]]) {
      await hold(page, { steer, handbrake: 1 });
      await page.waitForTimeout(1800);
      for (const [view, at] of [['chase', CHASE], ['side', SIDE], ['front', FRONT]]) await shot(page, `a-${name}-${view}`, at);
      report.views[name] = await driver(page);
      log(name, { reach: report.views[name]?.reach, headYawDeg: +(report.views[name]?.pose.headYaw * 57.3).toFixed(1) });
    }
    await hold(page, { horn: 1, handbrake: 1 });
    await page.waitForTimeout(800);
    await shot(page, 'a-horn-front', FRONT);
    await hold(page, null);
  }
  if (modes.has('brake') && straight) {
      await waitFps(page);
      await page.evaluate(view => window.__game.damage.look(view), SIDE);
      report.braking = await run(page, straight, 70, 'brake');
      await page.screenshot({ path: path.join(out, 'a-braking-side.png') }); report.shots.push(path.join(out, 'a-braking-side.png'));
      log('braking', { ...report.braking, pitchDeg: +(report.braking.pose?.pitch * 57.3).toFixed(1) });
      await page.waitForTimeout(1200);
      await hold(page, { brake: 1, handbrake: 1 });
      await page.waitForTimeout(2500);
  }
  if (modes.has('crash')) {
    // The clear straight ends at a solid hit (a building) when it is shorter than its 160 m search: crash into that;
    // else PROBE_WALL='{"x":..,"y":..,"z":..,"yaw":..,"distance":..}' or a square wall near the start car.
    const wall = straight && straight.length < 159 ? straight : process.env.PROBE_WALL ? JSON.parse(process.env.PROBE_WALL) : await findWall(page);
    log('wall', { wall });
    if (wall) {
      await waitFps(page);
      await page.evaluate(view => window.__game.damage.look(view), SIDE);
      report.crash = await run(page, wall, 45, 'coast');
      await page.screenshot({ path: path.join(out, 'a-crash-side.png') }); report.shots.push(path.join(out, 'a-crash-side.png'));
      log('crash', { ...report.crash, headPitchDeg: +(report.crash.pose?.headPitch * 57.3).toFixed(1), pitchDeg: +(report.crash.pose?.pitch * 57.3).toFixed(1) });
      await hold(page, { handbrake: 1 });
      await page.waitForTimeout(1500);
    }
  }
  if (modes.has('remote')) {
    // A second player in the room sees A's driver (its own browser context: its own session). B's remote look frames
    // the most damaged remote car, and a room keeps other players' parked cars: a light dent makes it A's.
    await hold(page, { steer: -1, handbrake: 1 });
    await page.evaluate(() => window.__game.damage.dent({ kmh: 20, at: 'rear' }));
    const b = await open('b', await browser.newContext());
    await b.waitForFunction(() => window.__game.drivers.snapshot().drivers.some(d => d.key.startsWith('remote:') && d.drawn), null, { timeout: 90000 })
      .catch(() => report.errors.push('b: no remote driver drawn within 90 s'));
    report.remote = await b.evaluate(() => window.__game.drivers.snapshot().drivers);
    await b.evaluate(() => window.__game.damage.look({ yaw: 40, pitch: 10, distance: 5, height: 0, remote: true }));
    await settle(b, 30);
    await b.screenshot({ path: path.join(out, 'b-remote-driver.png') }); report.shots.push(path.join(out, 'b-remote-driver.png'));
    await glass(b, false); await settle(b, 3);
    await b.screenshot({ path: path.join(out, 'b-remote-driver-noglass.png') }); report.shots.push(path.join(out, 'b-remote-driver-noglass.png'));
    log('remote', { drivers: report.remote.map(d => ({ key: d.key, drawn: d.drawn, reach: d.reach, headYawDeg: +(d.pose.headYaw * 57.3).toFixed(1) })) });
    await hold(page, null);
    await page.evaluate(() => window.__game.damage.clear());
    await b.close();
  }
  if (modes.has('perf')) {
    // 20 bench cars ahead, with and without animated drivers, alternating (PERF_ROUNDS pairs, default 5); a fixed
    // camera over them. Each pair is measured off then on, and the cost is the median of the pairs' differences: the
    // streaming world keeps changing the scene's own cost between rounds.
    await hold(page, { handbrake: 1 });
    await page.evaluate(() => window.__game.damage.look({ yaw: 180, pitch: 22, distance: 9, height: .5 }));
    report.perf = [];
    await page.evaluate(() => window.__game.damage.bench({ count: 20, kind: 'capable' }));
    report.settle = await settleDraws(page);
    log('settled', report.settle);
    const rounds = Number(process.env.PERF_ROUNDS ?? 5);
    for (let round = 0; round < rounds; round++) for (const on of [false, true]) {
      const seated = await benchDrivers(page, on);
      await page.waitForTimeout(2500);
      if (on && round === 0) { const file = path.join(out, 'perf-view.png'); await page.screenshot({ path: file }); report.shots.push(file); }
      await page.evaluate(() => window.__render?.perf?.reset?.());
      const f = await frames(page, 240);
      const p = await page.evaluate(() => { const s = window.__perf, passes = window.__render?.perf?.passSnapshot?.();
        return { gpu: s?.gpuMs?.p50, cpu: s?.cpuMs?.p50, draws: s?.drawCalls,
          passes: passes ? Object.fromEntries(Object.entries(passes.gpuMs ?? {}).map(([k, v]) => [k, v?.p50 == null ? null : +v.p50.toFixed(2)])) : null }; });
      const ms = await page.evaluate(() => window.__game.drivers.frameMs);
      const drawn = await page.evaluate(() => window.__game.drivers.snapshot().drivers.filter(d => d.drawn).length);
      const row = { round, drivers: on, seated, drawn, driversMs: +ms.toFixed(3), ...f, gpuP50: p.gpu, cpuP50: p.cpu, draws: p.draws, passes: p.passes };
      report.perf.push(row); log('perf', row);
    }
    await benchDrivers(page, false);
    await page.evaluate(() => window.__game.damage.bench({ count: 0 }));
    await page.evaluate(() => window.__game.damage.look(null));
    const median = a => { const s = [...a].filter(v => typeof v === 'number').sort((x, y) => x - y); return s.length ? +s[s.length >> 1].toFixed(2) : null; };
    const of = (on, key) => median(report.perf.filter(r => r.drivers === on).map(r => r[key]));
    const delta = key => median(report.perf.filter(r => r.drivers).map(r => r[key] - report.perf.find(o => o.round === r.round && !o.drivers)[key]));
    report.perfSummary = { frameP50Without: of(false, 'p50'), frameP50With: of(true, 'p50'), cpuP50Without: of(false, 'cpuP50'), cpuP50With: of(true, 'cpuP50'),
      gpuP50Without: of(false, 'gpuP50'), gpuP50With: of(true, 'gpuP50'), driversMsWith: of(true, 'driversMs'), drawsWithout: of(false, 'draws'), drawsWith: of(true, 'draws'),
      pairedDelta: { frameP50: delta('p50'), gpuP50: delta('gpuP50'), cpuP50: delta('cpuP50'), draws: delta('draws') } };
    log('perf summary', report.perfSummary);
  }
  if (modes.has('ambient')) {
    // Static drivers in the ambient traffic (actors/driver/ambient.ts): the same street with them and without,
    // alternating (PERF_ROUNDS pairs); run with traffic on: PROBE_URL without traffic=0.
    const ambient = on => page.evaluate(on => {
      const g = window.__game, d = g.drivers;
      d.__ambient ??= d.ambient;
      d.ambient = on ? d.__ambient : () => {};
      let root = [...d.drivers.values()][0]?.frame; while (root?.parent) root = root.parent;
      root?.traverse(o => { if (o.name.startsWith('ambient-drivers/') && !on) o.visible = false; });
    }, on);
    await hold(page, { handbrake: 1 });
    await page.evaluate(() => window.__game.damage.look({ yaw: 150, pitch: 14, distance: 9, height: 1 }));
    report.settle = await settleDraws(page);
    log('settled', report.settle);
    report.ambient = [];
    const rounds = Number(process.env.PERF_ROUNDS ?? 5);
    for (let round = 0; round < rounds; round++) for (const on of [false, true]) {
      await ambient(on);
      await page.waitForTimeout(2500);
      if (on && round === 0) { const file = path.join(out, 'ambient-view.png'); await page.screenshot({ path: file }); report.shots.push(file); }
      await page.evaluate(() => window.__render?.perf?.reset?.());
      const f = await frames(page, 240);
      const p = await page.evaluate(() => { const s = window.__perf; return { gpu: s?.gpuMs?.p50, cpu: s?.cpuMs?.p50, draws: s?.drawCalls }; });
      const seated = await page.evaluate(() => Object.values(window.__game.drivers.snapshot().ambient ?? {}).reduce((a, b) => a + b, 0));
      const row = { round, ambient: on, seated, ...f, gpuP50: p.gpu, cpuP50: p.cpu, draws: p.draws };
      report.ambient.push(row); log('ambient', row);
    }
    await ambient(true);
    await page.evaluate(() => window.__game.damage.look(null));
    const median = a => { const s = [...a].filter(v => typeof v === 'number').sort((x, y) => x - y); return s.length ? +s[s.length >> 1].toFixed(2) : null; };
    const delta = key => median(report.ambient.filter(r => r.ambient).map(r => r[key] - report.ambient.find(o => o.round === r.round && !o.ambient)[key]));
    report.ambientSummary = { seated: median(report.ambient.filter(r => r.ambient).map(r => r.seated)),
      pairedDelta: { frameP50: delta('p50'), gpuP50: delta('gpuP50'), cpuP50: delta('cpuP50'), draws: delta('draws') } };
    log('ambient summary', report.ambientSummary);
  }
  report.ok = report.errors.length === 0;
} catch (error) {
  report.ok = false; report.failure = String(error?.stack ?? error).slice(0, 1500);
}
report.seconds = (Date.now() - t0) / 1000;
fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(report, null, 1));
console.log(JSON.stringify({ ok: report.ok, failure: report.failure, errors: report.errors.slice(0, 5), out, seconds: report.seconds }));
await browser.close().catch(() => {});
