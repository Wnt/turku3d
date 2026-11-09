#!/usr/bin/env node
// Crash lab for multiplayer car collisions: two headed game windows side by side (A left, B right) in an empty flat
// world (the e2e fake coarse service, tests/e2e/coarseFake.ts), joined to a local room server (server/). Each scenario
// places both cars, drives them on exact straight paths at set speeds (the autopilot, released to coast just before
// contact) and records every frame on both pages: the page's own car (its owner's truth) and how it sees the other car
// (the simulated remote car, physics/world.ts REMOTE_SIM, and the drawn model). The report compares each car's truth
// with the other page's view of it, and a stationary target with the same hit on a local parked car (single player).
// Each player joins in its own room slot (its own start car, apart from the other's) and drives the car it was seated
// in. Before each scenario both cars go to separate holding spots and then to their starts, and the leg starts only
// once both pages agree where both cars stand. Both pages record every frame throughout; the report lists every car
// movement on either page that no placement explains (<name>.trace.json holds the frames).
//
//   node scripts/crash-lab.mjs [--only rear30,head25] [--keep] [--port 5226] [--mp-port 5229] [--out DIR]
//
// Serves this checkout with the Vite dev server, so a code change needs only a re-run. Windows stay open with --keep.
import { chromium } from '@playwright/test';
import { execFileSync, spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { chromeArgs, chromeExecutable, chromeIgnoreDefaultArgs } from '../../tools/dev/chrome-args.mjs';
import { cells, G, routeCoarse } from '../tests/e2e/coarseFake.ts';

const argv = process.argv.slice(2);
const opt = (name, fallback) => { const i = argv.indexOf(`--${name}`); return i >= 0 ? argv[i + 1] : fallback; };
const PORT = Number(opt('port', 5226)), MP_PORT = Number(opt('mp-port', 5229)), KEEP = argv.includes('--keep');
const ONLY = (opt('only', '') ?? '').split(',').filter(Boolean);
const OUT = path.resolve(opt('out', path.join(os.homedir(), 'turku3d/loops/mpsession/crashlab')));
const GAME = path.resolve(import.meta.dirname, '..'), REPO = path.resolve(GAME, '..');
const sleep = ms => new Promise(r => setTimeout(r, ms));
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

const children = [];
const stopChildren = () => { for (const c of children) if (c.exitCode === null) c.kill('SIGTERM'); };
process.on('exit', stopChildren);
process.on('SIGINT', () => { stopChildren(); process.exit(130); });
function start(name, cmd, args, opts) {
  const child = spawn(cmd, args, { ...opts, stdio: ['ignore', 'pipe', 'pipe'] });
  const file = fs.createWriteStream(path.join(OUT, `${name}.log`));
  child.stdout.pipe(file); child.stderr.pipe(file);
  children.push(child);
  return child;
}
async function waitHttp(url, ms) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { if ((await fetch(url)).ok) return; } catch { /* not up yet */ }
    await sleep(250);
  }
  throw new Error(`${url} did not answer in ${ms} ms`);
}

// --- Car geometry (golf7, the player model) and the scenarios. Engine frame: x east, z south, yaw 0 faces +x. ------
const HALF_L = 2.15, HALF_W = 0.9, Y = G + 0.75, KMH = 1 / 3.6;
/** A car: start pose, and for a driven one its speed and the distance it drives before it is released to coast. */
const car = (x, z, yaw, kmh = 0, release = 0) => ({ x, z, yaw, speed: kmh * KMH, release });
const dir = yaw => ({ x: Math.cos(yaw), z: -Math.sin(yaw) });
/** `run` m of run-up to the contact, released `gap` m before it. */
const toward = (cx, cz, yaw, kmh, contact, run = 45, gap = 1) => {
  const d = dir(yaw);
  return car(cx - d.x * (contact + run), cz - d.z * (contact + run), yaw, kmh, run - gap);
};
const SCENARIOS = [
  // A drives into the back of B, standing in the road with its driver in (no input).
  { name: 'rear30', what: 'A 30 km/h into the rear of a standing B', a: toward(0, 0, 0, 30, 2 * HALF_L), b: car(0, 0, 0), baseline: true },
  { name: 'rear30hb', what: 'A 30 km/h into the rear of B standing on its handbrake', a: toward(0, 0, 0, 30, 2 * HALF_L), b: { ...car(0, 0, 0), handbrake: true }, baseline: true },
  { name: 'side30', what: 'A 30 km/h into the side of a standing B', a: toward(0, 0, 0, 30, HALF_L + HALF_W), b: car(0, 0, -Math.PI / 2), baseline: true },
  { name: 'head25', what: 'A and B head-on, 25 km/h each', a: toward(0, 0, 0, 25, HALF_L), b: toward(0, 0, Math.PI, 25, HALF_L) },
  // B crosses at 25 km/h; A, equally fast, starts its run-up as much later as its front needs to meet B's side.
  { name: 'tbone25', what: 'A 25 km/h into the side of B crossing at 25 km/h', a: toward(0, 0, 0, 25, HALF_L + HALF_W, 45 + 0), b: toward(0, 0, -Math.PI / 2, 25, 0, 45, -3) },
  { name: 'rear50', what: 'A 50 km/h into the rear of a standing B', a: toward(0, 0, 0, 50, 2 * HALF_L, 70), b: car(0, 0, 0), baseline: true },
];
const FAR = car(0, 120, 0);
/** Holding spots off every scenario's paths: both cars go there first, so no placement lands on the other car. */
const HOLD = { A: car(-25, 30, 0), B: car(25, 30, 0) };

// --- Pages ----------------------------------------------------------------------------------------------------------
/** Installed in each page: records every frame (own car, how it sees the other car), places its own car (the one it
 *  drives), and runs one scenario leg from room time `at`. */
const LAB = () => {
  const g = window.__game, mp = g.multiplayerClient();
  const v2 = v => Array.isArray(v) ? { x: v[0], y: v[1], z: v[2] } : v ?? { x: 0, y: 0, z: 0 };
  const r3 = n => Math.round(n * 1000) / 1000;
  const yawOf = q => { const a = 2 * Math.atan2(q.y, q.w); return r3(Math.atan2(Math.sin(a), Math.cos(a))); };
  const lab = { trace: [], marks: [], leg: null, last: null };
  const frame = () => {
    requestAnimationFrame(frame);
    const now = mp.serverNow(), s = g.state(), v = s.vehicle;
    if (!v) return;
    const at = v2(v.pos), lv = v2(v.linvel), leg = lab.leg;
    if (leg && leg.car.speed > 0 && !leg.trip && now >= leg.at) {
      const end = { x: leg.car.x + leg.d.x * (leg.car.release + 5), z: leg.car.z + leg.d.z * (leg.car.release + 5) };
      leg.trip = g.autopilot.goTo(end, { mode: 'drive', path: [{ x: leg.car.x, z: leg.car.z }, end], speed: leg.car.speed, stop: false,
        arrive: 0.3, recoveries: 0, takeover: false, timeout: 60 });
    }
    if (leg?.trip && leg.released === null && (at.x - leg.car.x) * leg.d.x + (at.z - leg.car.z) * leg.d.z >= leg.car.release) {
      leg.trip.cancel('crash lab: coast into the contact'); leg.released = now;
    }
    const sim = (g.physics.latest?.remoteCars ?? [])[0];
    const vis = (s.multiplayer?.remoteVisuals ?? []).find(r => r.mode === 'car');
    const local = leg?.baseline ? g.physics.latest?.vehicles?.find(c => c.id === 'lab-parked') : null;
    const row = { t: Math.round(now), x: r3(at.x), z: r3(at.z), vx: r3(lv.x), vz: r3(lv.z), yaw: yawOf(v.rot),
      sim: sim ? { x: r3(sim.pos.x), z: r3(sim.pos.z), snaps: sim.snaps, ...(sim.held ? { held: 1 } : {}) } : null,
      drawn: vis ? { x: r3(vis.x), z: r3(vis.z) } : null,
      ...(leg ? { rel: leg.released !== null, drove: leg.car.speed > 0 } : {}),
      ...(local ? { local: { x: r3(v2(local.pos).x), z: r3(v2(local.pos).z), vx: r3(v2(local.linvel).x), vz: r3(v2(local.linvel).z) } } : {}) };
    lab.trace.push(row); lab.last = { ...row, id: v.id };
    if (leg) {
      leg.rows.push(row);
      if (now >= leg.at + leg.ms) { leg.trip?.cancel('crash lab: leg over'); lab.leg = null; leg.done(leg.rows); }
    }
  };
  requestAnimationFrame(frame);
  window.__lab = {
    place(p, y) {
      lab.marks.push({ t: Math.round(mp.serverNow()), place: p });
      g.physics.resetVehicle({ x: p.x, y, z: p.z }, p.yaw, g.state().vehicle?.id ?? 'player');
    },
    status: () => lab.last,
    drain() { const out = { trace: lab.trace, marks: lab.marks }; lab.trace = []; lab.marks = []; return out; },
    label(text) {
      let el = document.getElementById('crash-lab');
      if (!el) { el = Object.assign(document.createElement('div'), { id: 'crash-lab' });
        el.style.cssText = 'position:fixed;left:12px;bottom:12px;z-index:99999;padding:8px 12px;background:#000b;color:#fff;font:600 16px system-ui;border-radius:6px';
        document.body.append(el); }
      el.textContent = text;
    },
    run(leg) {
      return new Promise(done => { lab.leg = { ...leg, d: { x: Math.cos(leg.car.yaw), z: -Math.sin(leg.car.yaw) }, rows: [], trip: null, released: null, done }; });
    },
  };
};

/** Opens a headed game window for player `i` (A left, B right) up to the start screen. */
async function openPlayer(i, bounds) {
  const w = Math.floor(bounds.w / 2);
  const browser = await chromium.launch({ headless: false, executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs,
    args: chromeArgs(['--enable-unsafe-webgpu', '--enable-features=Vulkan,WebGPU', '--use-angle=metal', '--ignore-gpu-blocklist',
      '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows', '--disable-background-timer-throttling',
      `--window-position=${bounds.x + i * w},${bounds.y}`, `--window-size=${w},${bounds.h}`]) });
  const page = await (await browser.newContext({ viewport: null })).newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const drafts = {};
  for (let ti = -1; ti <= 1; ti++) for (let tj = -1; tj <= 1; tj++) drafts[`t0_${ti}_${tj}`] = { cells: cells(ti, tj) };
  await routeCoarse(page, drafts);
  await page.goto(`http://127.0.0.1:${PORT}/?backend=webgpu&traffic=0&walkers=0&telemetry=0&mp=${encodeURIComponent(`ws://127.0.0.1:${MP_PORT}/`)}`);
  await page.waitForFunction(() => window.__game?.ready, null, { timeout: 240000 });
  return { browser, page, errors, name: i ? 'B' : 'A' };
}

/** Joins the room, gets into the start car of its slot (which it stands beside) and drives it to its holding spot,
 *  so the next player's start car never lands on it. */
async function join(p, hold) {
  const { page } = p;
  for (let k = 0; k < 15 && await page.evaluate(() => window.__game.state().startScreen); k++) {
    await page.evaluate(() => { for (const b of document.querySelectorAll('.browser-warning button, [class*="warning"] button')) if (b.offsetParent) b.click(); });
    await page.locator('[data-option="multiplayer"]').first().click({ timeout: 5000 }).catch(() => {});
    await sleep(1500);
  }
  await page.waitForFunction(() => { const s = window.__game.state(); return !s.loading && !s.startScreen && s.multiplayer?.connected && s.grounded; },
    null, { timeout: 120000 });
  await page.evaluate(LAB);
  for (let k = 0; k < 5 && await page.evaluate(() => window.__game.state().mode) !== 'car'; k++) { await page.keyboard.press('KeyF'); await sleep(800); }
  if (await page.evaluate(() => window.__game.state().mode) !== 'car') throw new Error(`${p.name} could not get into its start car`);
  const seat = await page.evaluate(() => window.__lab.status());
  log(`${p.name} in its start car ${seat.id} at (${seat.x}, ${seat.z})`);
  await page.evaluate(([c, y]) => window.__lab.place(c, y), [hold, Y]);
}

// --- Analysis -------------------------------------------------------------------------------------------------------
const at = (rows, t) => {
  for (let i = 1; i < rows.length; i++) if (rows[i].t >= t) {
    const a = rows[i - 1], b = rows[i], f = Math.max(0, Math.min(1, (t - a.t) / Math.max(1, b.t - a.t)));
    return { x: a.x + (b.x - a.x) * f, z: a.z + (b.z - a.z) * f, vx: a.vx + (b.vx - a.vx) * f, vz: a.vz + (b.vz - a.vz) * f };
  }
  return null;
};
const q = (a, f) => { const s = a.filter(Number.isFinite).sort((x, y) => x - y); return s.length ? +s[Math.min(s.length - 1, Math.floor(f * s.length))].toFixed(2) : null; };
const kmh = v => v ? +(Math.hypot(v.vx, v.vz) * 3.6).toFixed(1) : null;
/** The first frame, once the car coasts (or, standing, once the leg runs), whose speed changed by more than 1.5 m/s
 *  over the last 50 ms: braking and engine drag change it by under 0.6. */
function contact(rows, from, key = null) {
  const v = r => { const o = key ? r[key] : r; return o ? { vx: o.vx, vz: o.vz } : null; };
  for (let i = 1, k = 0; i < rows.length; i++) {
    while (rows[i].t - rows[k].t > 50) k++;
    const r = rows[i], a = v(rows[k]), b = v(r);
    if (r.t < from || (r.drove && !r.rel) || !a || !b || k === i) continue;
    if (Math.hypot(b.vx - a.vx, b.vz - a.vz) > 1.5) return rows[k].t;
  }
  return null;
}
/** How page `rows` saw the other car (`view`: sim or drawn) against that car's own truth `truth`. */
function viewError(rows, truth, view, from, to) {
  const gaps = [];
  let jump = 0, prev = null;
  for (const r of rows) {
    if (r.t < from || r.t > to || !r[view]) continue;
    const want = at(truth, r.t);
    if (want) gaps.push(Math.hypot(r[view].x - want.x, r[view].z - want.z));
    if (prev && want) {
      const moved = Math.hypot(r[view].x - prev.x, r[view].z - prev.z), dt = (r.t - prev.t) / 1000;
      jump = Math.max(jump, moved - Math.hypot(want.vx, want.vz) * dt);
    }
    prev = { ...r[view], t: r.t };
  }
  return { p50: q(gaps, 0.5), p90: q(gaps, 0.9), max: q(gaps, 1), jump: +jump.toFixed(2), n: gaps.length };
}

/** Movements no placement explains, from page `name`'s frames: its own car moving more than its speed carries it, and
 *  its view of the other car (simulated, drawn) jumping, or standing more than 0.6 m from the other car's truth. A
 *  placement explains its own car's next 0.4 s and the other page's view of it for 1.5 s. Episodes in room time. */
function unexplained(name, own, other, ownMarks, otherMarks) {
  const after = (marks, t, ms) => marks.some(m => t >= m.t - 50 && t <= m.t + ms);
  const speed = r => Math.hypot(r.vx, r.vz);
  const found = [];
  const add = (what, t, m) => {
    const e = found.findLast(f => f.what === what);
    if (e && t - e.to < 150) { e.to = t; e.max = Math.max(e.max, m); e.n++; } else found.push({ what, from: t, to: t, max: m, n: 1 });
  };
  // Physics states (own car, simulated other car) advance in 60 Hz steps, one or two per frame: compared over 50 ms.
  // The drawn car is compared frame by frame, as it is seen.
  for (let i = 1, k = 0; i < own.length; i++) {
    while (own[i].t - own[k + 1].t >= 50) k++;
    const a = own[i - 1], b = own[i], w = own[k], dt = (b.t - a.t) / 1000, dw = (b.t - w.t) / 1000;
    if (dt <= 0) continue;
    if (dw >= 0.05) {
      const j = Math.hypot(b.x - w.x, b.z - w.z) - Math.max(speed(w), speed(b)) * dw;
      if (j > 0.15 && !after(ownMarks, b.t, 400)) add(`${name}'s own car jumps`, b.t, j);
    }
    if (after(otherMarks, b.t, 1500)) continue;
    const truth = at(other, b.t);
    if (!truth) continue;
    for (const key of ['sim', 'drawn']) {
      if (!b[key]) continue;
      const gap = Math.hypot(b[key].x - truth.x, b[key].z - truth.z);
      if (gap > 0.6) add(`${name}'s ${key === 'sim' ? 'simulated' : 'drawn'} other car off its truth`, b.t, gap);
      const from = key === 'sim' ? w : a, span = key === 'sim' ? dw : dt;
      if (from[key] && span > 0 && (key === 'drawn' || dw >= 0.05)) {
        const jj = Math.hypot(b[key].x - from[key].x, b[key].z - from[key].z) - Math.hypot(truth.vx, truth.vz) * span;
        if (jj > (key === 'drawn' ? 0.08 : 0.15)) add(`${name}'s ${key === 'sim' ? 'simulated' : 'drawn'} other car jumps`, b.t, jj);
      }
    }
  }
  return found;
}

/** Waits until each page's own car stands still at its target and the page sees the other car at the other's target
 *  (when that is within 60 m): both pages then agree where both cars are. */
async function settle(pages, targets, ms = 10000) {
  const t0 = Date.now();
  let last = [];
  while (Date.now() - t0 < ms) {
    last = await Promise.all(pages.map(p => p.page.evaluate(() => window.__lab.status())));
    const ok = last.every((st, i) => {
      const mine = targets[i], theirs = targets[1 - i], seen = st?.drawn ?? st?.sim;
      if (!st || Math.hypot(st.x - mine.x, st.z - mine.z) > 0.3 || Math.hypot(st.vx, st.vz) > 0.15) return false;
      if (Math.hypot(theirs.x - mine.x, theirs.z - mine.z) > 55) return true;
      // Near enough to collide with: simulated here (REMOTE_SIM), where the other page has it, and drawn there.
      return !!st.sim && !!seen && Math.hypot(st.sim.x - theirs.x, st.sim.z - theirs.z) < 0.4 && Math.hypot(seen.x - theirs.x, seen.z - theirs.z) < 0.4;
    });
    if (ok) { await sleep(500); return Date.now() - t0; }
    await sleep(100);
  }
  log(`  settle: not settled in ${ms} ms: ${JSON.stringify(last.map(st => st && { x: st.x, z: st.z, v: Math.hypot(st.vx, st.vz).toFixed(2), seen: st.drawn ?? st.sim }))}`);
  return null;
}

const legSnaps = rows => { const s = rows.filter(r => r.sim).map(r => r.sim.snaps); return s.length ? s.at(-1) - s[0] : '-'; };
function report(s, A, B, base, from, baseFrom) {
  const lines = [`${s.name}: ${s.what}`];
  const tA = contact(A, from), tB = contact(B, from), t0 = Math.min(tA ?? Infinity, tB ?? Infinity);
  if (!Number.isFinite(t0)) { lines.push('  no contact detected on either page'); }
  else {
    const before = t => ({ a: kmh(at(A, t - 150)), b: kmh(at(B, t - 150)) });
    const after = dt => ({ a: kmh(at(A, t0 + dt)), b: kmh(at(B, t0 + dt)) });
    const b0 = before(t0), a05 = after(500), a15 = after(1500);
    lines.push(`  contact on A's page ${tA === null ? 'none' : `${Math.round(tA - t0)} ms`}, on B's page ${tB === null ? 'none' : `${Math.round(tB - t0)} ms`} (after the first)`);
    lines.push(`  own speeds (km/h)  before: A ${b0.a}, B ${b0.b} | +0.5 s: A ${a05.a}, B ${a05.b} | +1.5 s: A ${a15.a}, B ${a15.b}`);
    if (base) {
      const tl = contact(base, baseFrom);
      const local = r => r ? kmh(r) : null;
      const pick = (dt, key) => { const r = base.filter(x => x.t >= (tl ?? 0) + dt)[0]; return key ? local(r?.[key]) : local(r); };
      lines.push(`  single player, same hit on a local parked car: before A ${kmh(at(base, (tl ?? 0) - 150))} | +0.5 s: A ${pick(500)}, parked ${pick(500, 'local')} | +1.5 s: A ${pick(1500)}, parked ${pick(1500, 'local')}`);
    }
    const ab = viewError(A, B, 'sim', t0 - 2000, t0 + 3000), ba = viewError(B, A, 'sim', t0 - 2000, t0 + 3000);
    const abd = viewError(A, B, 'drawn', t0 - 2000, t0 + 3000), bad = viewError(B, A, 'drawn', t0 - 2000, t0 + 3000);
    lines.push(`  A's simulated B vs B's truth, -2..+3 s: p50 ${ab.p50} p90 ${ab.p90} max ${ab.max} m; drawn p50 ${abd.p50} max ${abd.max} m, largest frame jump ${abd.jump} m`);
    lines.push(`  B's simulated A vs A's truth, -2..+3 s: p50 ${ba.p50} p90 ${ba.p90} max ${ba.max} m; drawn p50 ${bad.p50} max ${bad.max} m, largest frame jump ${bad.jump} m`);
  }
  const endA = A.at(-1), endB = B.at(-1);
  const seenB = endA?.sim ?? endA?.drawn, seenA = endB?.sim ?? endB?.drawn;
  lines.push(`  end: A at (${endA.x.toFixed(2)}, ${endA.z.toFixed(2)}), B at (${endB.x.toFixed(2)}, ${endB.z.toFixed(2)}); A sees B ${seenB ? Math.hypot(seenB.x - endB.x, seenB.z - endB.z).toFixed(2) : '-'} m off, B sees A ${seenA ? Math.hypot(seenA.x - endA.x, seenA.z - endA.z).toFixed(2) : '-'} m off; placements of the other car during the leg: A-page ${legSnaps(A)}, B-page ${legSnaps(B)}`);
  return lines.join('\n');
}

// --- Main -----------------------------------------------------------------------------------------------------------
const fixture = `
  import { ScoreStore } from './server/store.js';
  import { createServer } from './server/http.js';
  const app = createServer({ store: new ScoreStore(process.env.TURKU_MP_SCORES), origins: ['http://127.0.0.1:${PORT}'] });
  app.server.listen(${MP_PORT}, '127.0.0.1', () => console.log('listening ${MP_PORT}'));
  process.on('SIGTERM', async () => { await app.close(); process.exit(0); });`;
fs.mkdirSync(path.join(OUT, 'scores'), { recursive: true });
start('mp-server', process.execPath, ['--input-type=module', '-e', fixture], { cwd: REPO, env: { ...process.env, TURKU_MP_SCORES: path.join(OUT, 'scores') } });
start('vite', process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(PORT), '--strictPort'], { cwd: GAME, env: { ...process.env } });
await waitHttp(`http://127.0.0.1:${PORT}/`, 60000);
log(`game on :${PORT}, room server on :${MP_PORT}`);

process.env.TMPDIR = path.join(OUT, 'browser-tmp');
fs.mkdirSync(process.env.TMPDIR, { recursive: true });
const desk = execFileSync('osascript', ['-e', 'tell application "Finder" to get bounds of window of desktop']).toString().split(',').map(Number);
const bounds = { x: 0, y: 25, w: desk[2], h: desk[3] - 25 };
const [pa, pb] = await Promise.all([openPlayer(0, bounds), openPlayer(1, bounds)]);
// One after the other: A drives its start car away before B's start car appears (the start spot is the same).
await join(pa, HOLD.A);
await join(pb, HOLD.B);
await Promise.all([pa, pb].map(p => p.page.waitForFunction(() => window.__game.state().multiplayer?.players === 2, null, { timeout: 60000 })));
log(`both players in their cars at their holding spots: ${(await settle([pa, pb], [HOLD.A, HOLD.B])) === null ? 'NOT settled' : 'settled'}`);
for (const p of [pa, pb]) await p.page.evaluate(() => window.__lab.drain());

const results = [];
for (const s of SCENARIOS.filter(s => !ONLY.length || ONLY.includes(s.name))) {
  for (const p of [pa, pb]) await p.page.evaluate(t => window.__lab.label(t), `${s.name}: ${s.what}`);
  const legMs = Math.max(...[s.a, s.b].map(c => c.speed > 0 ? (c.release + 10) / c.speed * 1000 : 0)) + 5000;
  // Multiplayer: both cars to their holding spots, then to their starts; each step waits until both pages agree where
  // both cars stand. Then both legs from the same room time.
  await Promise.all([pa.page.evaluate(([c, y]) => window.__lab.place(c, y), [HOLD.A, Y]), pb.page.evaluate(([c, y]) => window.__lab.place(c, y), [HOLD.B, Y])]);
  await settle([pa, pb], [HOLD.A, HOLD.B]);
  await Promise.all([pa.page.evaluate(([c, y]) => window.__lab.place(c, y), [s.a, Y]), pb.page.evaluate(([c, y]) => window.__lab.place(c, y), [s.b, Y])]);
  await settle([pa, pb], [s.a, s.b]);
  const startAt = await pa.page.evaluate(() => window.__game.multiplayerClient().serverNow() + 800);
  if (s.b.handbrake) await pb.page.keyboard.down('Space');
  const [A, B] = await Promise.all([pa.page.evaluate(leg => window.__lab.run(leg), { car: s.a, at: startAt, ms: legMs }),
    pb.page.evaluate(leg => window.__lab.run(leg), { car: s.b, at: startAt, ms: legMs })]);
  if (s.b.handbrake) await pb.page.keyboard.up('Space');
  fs.writeFileSync(path.join(OUT, `${s.name}.json`), JSON.stringify({ scenario: s, A, B }));
  const [trA, trB] = await Promise.all([pa, pb].map(p => p.page.evaluate(() => window.__lab.drain())));
  // Single player: B's car far away, a local parked car of the same model where B stood (once A no longer sees B
  // there), the same leg for A.
  let base = null;
  if (s.baseline) {
    await Promise.all([pb.page.evaluate(([c, y]) => window.__lab.place(c, y), [FAR, Y]), pa.page.evaluate(([c, y]) => window.__lab.place(c, y), [HOLD.A, Y])]);
    await settle([pa, pb], [HOLD.A, FAR]);
    await pa.page.evaluate(([c, y]) => window.__lab.place(c, y), [s.a, Y]);
    await settle([pa, pb], [s.a, FAR]);
    await pa.page.evaluate(async ([b, y]) => {
      const { modelTuning, modelDamageBox } = await import('/src/vehicle/modelTuning.ts');
      const { PLAYER_MODEL } = await import('/src/actors/fleet/catalog.ts');
      window.__game.physics.removeVehicle('lab-parked');
      window.__game.physics.spawnVehicle({ x: b.x, y, z: b.z }, b.yaw, { id: 'lab-parked', kind: 'car', tuning: modelTuning(PLAYER_MODEL), damage: { box: modelDamageBox(PLAYER_MODEL) } });
    }, [s.b, Y]);
    await sleep(1500);
    const t = await pa.page.evaluate(() => window.__game.multiplayerClient().serverNow() + 500);
    base = await pa.page.evaluate(leg => window.__lab.run(leg), { car: s.a, at: t, ms: legMs, baseline: true });
    await pa.page.evaluate(() => window.__game.physics.removeVehicle('lab-parked'));
  }
  // Where both cars stood as the leg started, on both pages, against the scenario.
  const first = rows => rows.find(r => r.t >= startAt) ?? rows[0];
  const a0 = first(A), b0 = first(B), seen = r => r.drawn ?? r.sim;
  const pose = (r, c) => `(${r.x.toFixed(2)}, ${r.z.toFixed(2)}) yaw ${r.yaw.toFixed(2)}, ${Math.hypot(r.x - c.x, r.z - c.z).toFixed(2)} m / ${Math.abs(Math.atan2(Math.sin(r.yaw - c.yaw), Math.cos(r.yaw - c.yaw))).toFixed(2)} rad from its start`;
  const startLine = `  start: A ${pose(a0, s.a)}; B ${pose(b0, s.b)}; A sees B ${seen(a0) ? Math.hypot(seen(a0).x - b0.x, seen(a0).z - b0.z).toFixed(2) : '-'} m off, B sees A ${seen(b0) ? Math.hypot(seen(b0).x - a0.x, seen(b0).z - a0.z).toFixed(2) : '-'} m off`;
  const odd = [...unexplained('A', trA.trace, trB.trace, trA.marks, trB.marks), ...unexplained('B', trB.trace, trA.trace, trB.marks, trA.marks)]
    .sort((x, y) => x.from - y.from);
  const phase = t => t < startAt ? 'placing' : t < startAt + 1000 ? 'leg start' : 'leg';
  const oddLines = odd.length ? odd.slice(0, 12).map(e => `    ${phase(e.from)} ${((e.from - startAt) / 1000).toFixed(2)}..${((e.to - startAt) / 1000).toFixed(2)} s: ${e.what}, max ${e.max.toFixed(2)} m over ${e.n} frames`)
    : ['    none'];
  const text = [report(s, A, B, base, startAt, base ? base[0].t + 300 : 0), startLine, `  movements no placement explains (s from the leg start):`, ...oddLines].join('\n');
  console.log(text);
  results.push({ scenario: s, A, B, base, text });
  fs.writeFileSync(path.join(OUT, `${s.name}.json`), JSON.stringify({ scenario: s, A, B, base }));
  const [btA, btB] = await Promise.all([pa, pb].map(p => p.page.evaluate(() => window.__lab.drain())));
  fs.writeFileSync(path.join(OUT, `${s.name}.trace.json`), JSON.stringify({ scenario: s, startAt, A: trA, B: trB, baseline: { A: btA, B: btB } }));
}
fs.writeFileSync(path.join(OUT, 'report.txt'), results.map(r => r.text).join('\n\n') + '\n');
for (const p of [pa, pb]) if (p.errors.length) console.log(`${p.name} page errors: ${[...new Set(p.errors)].slice(0, 5).join(' | ')}`);
log(`report: ${path.join(OUT, 'report.txt')}`);
if (KEEP) { log('windows stay open (Ctrl-C ends the lab)'); await new Promise(() => {}); }
for (const p of [pa, pb]) await p.browser.close();
stopChildren();
