// Wreckfest 2 handling on the deployed share (vehicle/README.md "Tyres and handling"): the starter car on its
// slip-curve tyres, driven by a scripted virtual pad (analog stick and triggers, like a DualSense) and by the keyboard.
//   ~/turku3d/tools/host-lease/session.py run air car-handling --kind func --headless --load-timeout 180 \
//     -- node game/scripts/handling-probe.mjs OUT_DIR
// Runs: a closed-loop skidpad (pure pursuit on a circle in the page, speed steps to the grip limit) on the largest
// flat clear disc near the start; on the longest flat straight a keyboard brake test from 80 km/h (S held) and a held
// A key at 50 km/h (the keyboard slew). Writes OUT_DIR/result.json and PNGs; prints one JSON line.
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';

const out = path.resolve(process.argv[2] ?? 'handling-probe');
// A small viewport: the page's controller and samples run at its frame rate.
const url = process.env.PROBE_URL ?? 'https://city.lab.madekivi.fi/?dev=1&time=13:00&weather=clear&traffic=0&walkers=0';
fs.mkdirSync(out, { recursive: true });
const report = { url, errors: [], shots: [], steps: [] }, t0 = Date.now();
const log = (step, data = {}) => { const e = { t: +((Date.now() - t0) / 1000).toFixed(1), step, ...data }; report.steps.push(e); console.error(JSON.stringify(e)); };
const browser = await chromium.connectOverCDP(process.env.BROWSER_CDP, { timeout: 30000 });
const context = browser.contexts()[0] ?? await browser.newContext();

const page = await context.newPage();
await page.setViewportSize({ width: 640, height: 360 });
// A virtual standard-mapping pad the probe drives (window.__pad); hidden until __padOn, so the keyboard runs see none.
await page.addInitScript(() => {
  const pad = { id: 'Virtual DualSense Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 0ce6)', index: 0, connected: true,
    mapping: 'standard', timestamp: 0, axes: [0, 0, 0, 0], buttons: Array.from({ length: 17 }, () => ({ pressed: false, touched: false, value: 0 })) };
  window.__pad = pad; window.__padOn = false;
  Object.defineProperty(navigator, 'getGamepads', { value: () => (window.__padOn ? [pad] : []) });
});
await (await context.newCDPSession(page)).send('Emulation.setFocusEmulationEnabled', { enabled: true });
page.on('pageerror', e => report.errors.push(`pageerror: ${e.message}`.slice(0, 300)));
page.on('console', m => { const t = m.text(); if (/GPUValidationError|TypeError|ReferenceError/.test(t)) report.errors.push(`console: ${t}`.slice(0, 300)); });
await page.goto(`${url}&probe=${Date.now()}`, { waitUntil: 'domcontentloaded', timeout: 90000 });
await page.waitForFunction(() => window.__game?.ready, null, { timeout: 240000 });
await page.getByText('Multiplayer', { exact: true }).click();
await page.waitForFunction(() => { const s = window.__game.state?.(); return s && !s.startScreen && !s.loading; }, null, { timeout: 180000 });
report.release = await page.evaluate(() => fetch('/version.json').then(r => r.json()).then(v => v.sha).catch(() => null));
log('playing', { release: report.release });
await page.evaluate(() => window.__game.enterVehicle('player'));
await page.waitForTimeout(1500);

/** The largest flat disc near the start with no solid hit at bumper height out to its radius (16 directions), the
 *  ground on each ring within 0.5 m and flat; candidate centres on rings every 12 m out to 240 m. With `materials`,
 *  only ground of those materials (loose surfaces: a lawn is less flat, so its slope may reach 11°). */
const findDisc = (materials = null) => page.evaluate(async materials => {
  const ok = h => !!h && (materials ? materials.includes(h.material) && h.normal.y > .98 : h.normal.y > .995);
  const physics = window.__game.physics, car = physics.latest.vehicles.find(v => v.id === 'player');
  const ray = (o, d, max) => physics.raycast(o, d, max, 'solid');
  let best = null;
  for (let r = 0; r <= 240 && !(best?.R >= 26); r += 12) {
    const n = r ? Math.round(2 * Math.PI * r / 12) : 1;
    for (let a = 0; a < n && !(best?.R >= 26); a++) {
      const cx = car.pos.x + r * Math.cos(2 * Math.PI * a / n), cz = car.pos.z + r * Math.sin(2 * Math.PI * a / n);
      const g = await ray({ x: cx, y: car.pos.y + 30, z: cz }, { x: 0, y: -1, z: 0 }, 60);
      if (!ok(g) || Math.abs(g.point.y - car.pos.y) > 8) continue;
      let R = 0;
      for (const rr of materials ? [8, 10, 12, 14, 18] : [10, 14, 18, 22, 26]) {
        const dirs = Array.from({ length: 16 }, (_, k) => ({ x: Math.cos(k * Math.PI / 8), y: 0, z: Math.sin(k * Math.PI / 8) }));
        const walls = await Promise.all(dirs.map(d => ray({ x: cx, y: g.point.y + .6, z: cz }, d, rr)));
        if (walls.some(Boolean)) break;
        const ground = await Promise.all(dirs.map(d => ray({ x: cx + d.x * rr, y: g.point.y + 4, z: cz + d.z * rr }, { x: 0, y: -1, z: 0 }, 8)));
        if (ground.some(h => !ok(h) || Math.abs(h.point.y - g.point.y) > .5)) break;
        R = rr;
      }
      if (R > (best?.R ?? 0)) best = { x: cx, y: g.point.y, z: cz, R, material: g.material };
    }
  }
  return best;
}, materials);

/** The longest flat corridor (no solid hit at bumper height, ground level and flat) near the start, up to 230 m. */
const findStraight = () => page.evaluate(async () => {
  const physics = window.__game.physics, car = physics.latest.vehicles.find(v => v.id === 'player');
  const ray = (o, d, max) => physics.raycast(o, d, max, 'solid');
  let best = null;
  const starts = [[0, 0]];
  for (const r of [25, 50, 80]) for (let a = 0; a < 8; a++) starts.push([r * Math.cos(a * Math.PI / 4), r * Math.sin(a * Math.PI / 4)]);
  for (const [ox, oz] of starts) {
    const ground = await ray({ x: car.pos.x + ox, y: car.pos.y + 30, z: car.pos.z + oz }, { x: 0, y: -1, z: 0 }, 60);
    if (!ground || Math.abs(ground.point.y - car.pos.y) > 6 || ground.normal.y < .995) continue;
    for (let deg = 0; deg < 360; deg += 5) {
      const yaw = deg * Math.PI / 180, d = { x: Math.cos(yaw), y: 0, z: -Math.sin(yaw) }, side = { x: -d.z, y: 0, z: d.x };
      const hits = await Promise.all([0, -.9, .9].map(k => ray({ x: ground.point.x + side.x * k, y: ground.point.y + .55, z: ground.point.z + side.z * k }, d, 230)));
      const clear = Math.min(230, ...hits.map(h => h ? h.toi : 230));
      if (clear <= (best?.length ?? 100)) continue;
      let usable = 0, last = ground.point.y;
      for (let t = 4; t < clear - 3; t += 4) {
        const at = await ray({ x: ground.point.x + d.x * t, y: ground.point.y + 4, z: ground.point.z + d.z * t }, { x: 0, y: -1, z: 0 }, 9);
        if (!at || at.normal.y < .99 || Math.abs(at.point.y - last) > .06 || Math.abs(at.point.y - ground.point.y) > 1.5) break;
        last = at.point.y; usable = t;
      }
      if (usable > (best?.length ?? 100)) best = { x: ground.point.x, y: ground.point.y, z: ground.point.z, yaw, deg, length: usable };
    }
  }
  return best;
});

/** In-page skidpad: the car starts on the circle (centre to its left) and a pure-pursuit controller with an integral
 *  on the radial error steers the virtual pad's stick (inverting the stick curve and the starter car's speed lock)
 *  while a PI holds each speed step on the triggers. Per step: means over its last 2 s. */
const skidpad = (disc, R0, speeds) => page.evaluate(async ({ disc, R0, speeds }) => {
  const g = window.__game, physics = g.physics, pad = window.__pad;
  const car = () => physics.latest.vehicles.find(v => v.id === 'player');
  const heading = q => Math.atan2(2 * (q.w * q.y + q.x * q.z), 1 - 2 * (q.y * q.y + q.z * q.z));
  // Starter golf7 (vehicle/modelTuning.ts): wheelbase 2.633 m, rear axle 1.366 m behind the origin, lock 29.4° / (1 + (v/40)²).
  const L = 2.633, b = 1.366, maxSteer = Math.atan(2.6328 / (10.9 / 2 - 1.549 / 2)), lock = v => maxSteer / (1 + (v / 40) ** 2);
  const stick = s => { const m = Math.min(1, Math.abs(s)); return m < 1e-4 ? 0 : -Math.sign(s) * (0.16 + 0.84 * m ** (1 / 1.35)); };
  const P = { x: disc.x, z: disc.z + R0 };
  physics.resetVehicle({ x: P.x, y: disc.y + .75, z: P.z }, 0, 'player');
  await new Promise(r => setTimeout(r, 1500));
  window.__padOn = true;
  const rows = [];
  let I = 0, iT = 0, prev = null;
  for (const [k, kmh] of speeds.entries()) {
    const target = kmh / 3.6, was = k ? speeds[k - 1] / 3.6 : target, dur = k ? 7 : 9, start = performance.now(), win = [];
    await new Promise(resolve => {
      const tick = () => {
        const v = car(), now = performance.now(), t = (now - start) / 1000;
        if (!v) { requestAnimationFrame(tick); return; }
        const psi = heading(v.rot), qx = v.pos.x - P.x, qz = v.pos.z - P.z, speed = v.forwardSpeed;
        const rx = qx - b * Math.cos(psi), rz = qz + b * Math.sin(psi);
        const phi = Math.atan2(rx, R0 + rz), ahead = phi + Math.min(12, Math.max(4, 0.5 * speed)) / R0;
        const tx = R0 * Math.sin(ahead), tz = -(R0 - R0 * Math.cos(ahead));
        let a = Math.atan2(-(tz - rz), tx - rx) - psi; a = Math.atan2(Math.sin(a), Math.cos(a));
        const err = Math.hypot(qx, qz + R0) - R0, dt = prev ? (now - prev.now) / 1000 : 1 / 60;
        const delta = Math.atan(2 * L * Math.sin(a) / Math.hypot(tx - rx, tz - rz)) + I;
        if (Math.abs(delta) < lock(speed)) I = Math.max(-.3, Math.min(.3, I + 0.04 * err * dt));
        pad.axes[0] = stick(delta / lock(Math.max(1, speed)));
        const want = was + (target - was) * Math.min(1, t / 2), e = want - speed;
        iT = Math.max(-.15, Math.min(1, iT + 0.5 * e * dt));
        const thr = Math.max(-.15, Math.min(1, 0.6 * e + iT));
        pad.buttons[7].value = Math.max(0, thr); pad.buttons[7].pressed = thr > 0.05;
        pad.buttons[6].value = Math.max(0, -thr); pad.buttons[6].pressed = thr < -0.05;
        pad.timestamp = now;
        if (prev && t > dur - 2) {
          let dpsi = psi - prev.psi; dpsi = Math.atan2(Math.sin(dpsi), Math.cos(dpsi));
          win.push({ v: speed, r: dpsi / dt, steer: (v.wheels[0].steer + v.wheels[1].steer) / 2, err, lat: v.wheels.map(w => w.tyre?.lateral ?? 0) });
        }
        prev = { now, psi };
        if (t < dur) requestAnimationFrame(tick); else resolve();
      };
      requestAnimationFrame(tick);
    });
    const mean = f => win.reduce((s, x) => s + f(x), 0) / Math.max(1, win.length);
    const v = mean(x => x.v), r = mean(x => x.r), steer = mean(x => x.steer) * 180 / Math.PI;
    const row = { target: kmh, kmh: +(v * 3.6).toFixed(1), radius: +(v / r).toFixed(2), latG: +(v * r / 9.81).toFixed(3), steerDeg: +steer.toFixed(2),
      ackermannDeg: +(L * r / v * 180 / Math.PI).toFixed(2), radialErr: +mean(x => x.err).toFixed(2), errMax: +Math.max(...win.map(x => Math.abs(x.err))).toFixed(2),
      frames: win.length, held: Math.abs(v * 3.6 - kmh) < 2 && Math.abs(mean(x => x.err)) < 0.8 };
    rows.push(row);
    if (!row.held) break;
  }
  pad.axes[0] = 0; pad.buttons[7].value = 0; pad.buttons[7].pressed = false; pad.buttons[6].value = 1; pad.buttons[6].pressed = true;
  await new Promise(r => setTimeout(r, 2500));
  pad.buttons[6].value = 0; pad.buttons[6].pressed = false; window.__padOn = false;
  return rows;
}, { disc, R0, speeds });

/** A keyboard run on the straight: W to `kmh`, then `then` (brake: S held to a stop; turn: A held 1.2 s at `kmh`).
 *  Logged at every animation frame from the worker's latest state. */
async function keyRun(s, kmh, then) {
  await page.evaluate(s => window.__game.physics.resetVehicle({ x: s.x, y: s.y + .75, z: s.z }, s.yaw, 'player'), s);
  await page.waitForTimeout(1800);
  await page.evaluate(() => {
    const log = window.__probeLog = [];
    const tick = () => { const v = window.__game.physics.latest?.vehicles.find(x => x.id === 'player');
      if (v) log.push({ t: performance.now() / 1000, v: v.forwardSpeed, x: v.pos.x, z: v.pos.z, rot: v.rot, steer: (v.wheels[0].steer + v.wheels[1].steer) / 2 });
      if (log.length < 6000 && !window.__probeStop) requestAnimationFrame(tick); };
    window.__probeStop = false; requestAnimationFrame(tick);
  });
  const state = () => page.evaluate(s => { const v = window.__game.physics.latest.vehicles.find(x => x.id === 'player');
    return { v: v.forwardSpeed, along: (v.pos.x - s.x) * Math.cos(s.yaw) - (v.pos.z - s.z) * Math.sin(s.yaw) }; }, s);
  const start = Date.now();
  await page.keyboard.down('KeyW');
  for (let st = await state(); st.v < kmh / 3.6 && st.along < s.length - 25 - st.v * st.v / 20 && Date.now() - start < 20000; st = await state()) await page.waitForTimeout(15);
  const reached = (await state()).v * 3.6;
  let cmd = null;
  if (then === 'brake') {
    await page.keyboard.up('KeyW');
    cmd = await page.evaluate(() => performance.now() / 1000);
    await page.keyboard.down('KeyS');
    for (let i = 0; i < 400 && (await state()).v > .3; i++) await page.waitForTimeout(15);
    await page.keyboard.up('KeyS');
  } else {
    cmd = await page.evaluate(() => performance.now() / 1000);
    await page.keyboard.down('KeyA'); await page.waitForTimeout(1200); await page.keyboard.up('KeyA');
    await page.keyboard.up('KeyW');
    await page.keyboard.down('KeyS'); for (let i = 0; i < 300 && (await state()).v > .3; i++) await page.waitForTimeout(15); await page.keyboard.up('KeyS');
  }
  await page.waitForTimeout(600);
  const trace = await page.evaluate(() => { window.__probeStop = true; return window.__probeLog; });
  return { reached: +reached.toFixed(1), cmd, trace };
}

const heading = q => Math.atan2(2 * (q.w * q.y + q.x * q.z), 1 - 2 * (q.y * q.y + q.z * q.z));
function brakeStats({ reached, cmd, trace }) {
  const after = trace.filter(e => e.t >= cmd), v0 = after[0]?.v ?? 0;
  // The build-up: mean deceleration over the first 0.1, 0.2 and 0.3 s after the press.
  const by = s => { const e = after.find(x => x.t >= cmd + s); return e ? +((v0 - e.v) / (e.t - cmd) / 9.81).toFixed(2) : null; };
  const cross = level => { for (let i = 1; i < after.length; i++) if (after[i].v <= level) {
    const a = after[i - 1], b = after[i]; return a.t + (b.t - a.t) * (a.v - level) / (a.v - b.v); } return NaN; };
  const t90 = cross(.9 * v0), t10 = cross(.1 * v0), tStop = cross(.3);
  const decel = []; for (let i = 3; i < after.length; i++) { const a = after[i - 3], b = after[i]; if (b.t > a.t) decel.push({ t: b.t, g: (a.v - b.v) / (b.t - a.t) / 9.81 }); }
  const peak = Math.max(...decel.map(d => d.g)), rise = decel.find(d => d.g >= .9 * peak);
  const stop = after.find(e => e.v <= .3) ?? after.at(-1), first = after[0];
  return { startKmh: +(v0 * 3.6).toFixed(1), reached, meanDecelG: +(.8 * v0 / (t10 - t90) / 9.81).toFixed(3), peakG: +peak.toFixed(3),
    toPeak90S: rise ? +(rise.t - cmd).toFixed(3) : null, meanG: [by(.1), by(.2), by(.3)], stopM: +Math.hypot(stop.x - first.x, stop.z - first.z).toFixed(2), stopS: +(tStop - cmd).toFixed(2),
    samples: after.length };
}
function turnStats({ reached, cmd, trace }) {
  const after = trace.filter(e => e.t >= cmd && e.t <= cmd + 1.25), lock = Math.max(...after.map(e => Math.abs(e.steer)));
  const at90 = after.find(e => Math.abs(e.steer) >= .9 * lock);
  let peakYaw = 0;
  for (let i = 1; i < after.length; i++) { const d = heading(after[i].rot) - heading(after[i - 1].rot), dt = after[i].t - after[i - 1].t;
    if (dt > 0) peakYaw = Math.max(peakYaw, Math.abs(Math.atan2(Math.sin(d), Math.cos(d)) / dt) * 180 / Math.PI); }
  return { kmh: reached, wheelDeg: +(lock * 180 / Math.PI).toFixed(1), steerTo90S: at90 ? +(at90.t - cmd).toFixed(3) : null,
    slewDegPerS: at90 ? +(.9 * lock * 180 / Math.PI / (at90.t - cmd)).toFixed(1) : null, peakYawDegPerS: +peakYaw.toFixed(1) };
}

try {
  const fps = await page.evaluate(() => new Promise(r => { let n = 0; const s = performance.now(); const f = () => (++n < 120 ? requestAnimationFrame(f) : r(n / ((performance.now() - s) / 1000))); requestAnimationFrame(f); }));
  report.fps = +fps.toFixed(1); log('fps', { fps: report.fps });
  const disc = report.disc = await findDisc();
  log('disc', disc ?? {});
  if (disc && disc.R >= 14) {
    const R0 = Math.min(20, disc.R - 4);
    const speeds = [20, 25, 30, 35, 40, 45, 50, 55, 60].filter(k => (k / 3.6) ** 2 / R0 < 1.3 * 9.81);
    report.skidpad = { R: R0, rows: await skidpad(disc, R0, speeds) };
    log('skidpad', report.skidpad);
    const held = report.skidpad.rows.filter(r => r.held), lin = held.filter(r => r.latG < .5);
    if (lin.length >= 2) {
      const mx = lin.reduce((s, r) => s + r.latG, 0) / lin.length, my = lin.reduce((s, r) => s + r.steerDeg - r.ackermannDeg, 0) / lin.length;
      report.skidpad.understeerDegPerG = +(lin.reduce((s, r) => s + (r.latG - mx) * (r.steerDeg - r.ackermannDeg - my), 0) / lin.reduce((s, r) => s + (r.latG - mx) ** 2, 0)).toFixed(2);
    }
    report.skidpad.maxSteadyG = held.length ? Math.max(...held.map(r => r.latG)) : null;
    const file = path.join(out, 'skidpad.png'); await page.screenshot({ path: file }); report.shots.push(file);
  }
  // Loose ground (gravel, grass, soil, sand, low vegetation: shared/col/materials.ts MAT 9-12, 29), slip-curve surfaces.
  const loose = report.loose = await findDisc([9, 10, 11, 12, 29]);
  log('loose disc', loose ?? {});
  if (loose && loose.R >= 10) {
    const R0 = loose.R - 3;
    report.looseSkidpad = { R: R0, material: loose.material, rows: await skidpad(loose, R0, [15, 20, 25, 30, 35, 40].filter(k => (k / 3.6) ** 2 / R0 < 1.2 * 9.81)) };
    const held = report.looseSkidpad.rows.filter(r => r.held);
    report.looseSkidpad.maxSteadyG = held.length ? Math.max(...held.map(r => r.latG)) : null;
    log('loose skidpad', report.looseSkidpad);
  }
  const straight = report.straight = await findStraight();
  log('straight', straight ?? {});
  if (straight) {
    report.brakes = [];
    for (const kmh of [80, 50]) if (straight.length > 40 + (kmh / 3.6) ** 2 / 4) {
      const run = brakeStats(await keyRun(straight, kmh, 'brake')); report.brakes.push(run); log('brake', run);
    }
    if (straight.length > 80) { report.keyTurn = turnStats(await keyRun(straight, 50, 'turn')); log('key turn', report.keyTurn); }
    const file = path.join(out, 'straight.png'); await page.screenshot({ path: file }); report.shots.push(file);
  }
} catch (e) {
  report.errors.push(`probe: ${e.message}`.slice(0, 400));
}
fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(report, null, 1));
console.log(JSON.stringify({ release: report.release, fps: report.fps, disc: report.disc, skidpad: report.skidpad, loose: report.looseSkidpad, brakes: report.brakes, keyTurn: report.keyTurn, errors: report.errors.length }));
await page.close();
await browser.close().catch(() => {});
