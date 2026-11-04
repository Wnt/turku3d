// Multiplayer autopilot test: N headless game clients (separate browser contexts in one Chrome) against a local
// multiplayer server. Every client drives its own car with the autopilot: first out to one of four points around the
// spawn, then across to the opposite one, so routes cross. One client leaves mid-drive and a new one joins.
// Every 250 ms each client reports its own pose and how it sees the others; the analysis compares each remote view
// with that player's own pose at the same wall time (divergence), finds the delay that fits best (latency), and
// counts car contacts near another player.
//
// Node bots (game/tests/drive/mpbot.ts: the same follower on the real vehicle physics, protocol v2) fill the room
// beyond the browser clients at a fraction of a browser's cost; their own poses are ground truth too.
//
//   node tools/drivetest/mp.mjs OUT_DIR [--clients 2] [--bots 6] [--base http://localhost:5480/] [--port 5482]
//     [--seconds 150] [--sweep ~/turku3d/loops/autopilot/sweep]   (the bots' tile copy and tile-service.json)
//     [--cdp http://127.0.0.1:9362]   browsers on another host (Windows, the Air) through an SSH tunnel; that host
//                                     reaches the game and the server through ssh -R of the base and server ports
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, execFileSync } from 'node:child_process';
import { chromium } from '../../game/node_modules/@playwright/test/index.mjs';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../dev/chrome-args.mjs';

const args = process.argv.slice(2);
const out = args[0];
const opt = (n, d) => { const i = args.indexOf(`--${n}`); return i >= 0 ? args[i + 1] : d; };
const B = Number(opt('bots', 6)), sweepDir = opt('sweep', path.join(os.homedir(), 'turku3d/loops/autopilot/sweep'));
const N = Number(opt('clients', 2)), base = opt('base', 'http://localhost:5480/'), mpPort = Number(opt('port', 5482));
const seconds = Number(opt('seconds', 150));
fs.mkdirSync(out, { recursive: true });
const guard = cmd => { try { execFileSync('python3', [path.join(os.homedir(), 'turku3d/tools/chrome-guard/guard.py'), cmd, 'autopilot'], { stdio: 'ignore' }); return 0; } catch (e) { return e.status ?? 1; } };
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

// The multiplayer server, isolated: own port, temporary scores, no tile producer pings.
const scores = fs.mkdtempSync(path.join(os.tmpdir(), 'autopilot-mp-'));
const server = spawn(process.execPath, ['server/index.js'], { cwd: path.resolve(import.meta.dirname, '../..'),
  env: { ...process.env, TURKU_MP_PORT: String(mpPort), TURKU_MP_SCORES: scores, TURKU_MP_PRODUCER_PORTS: '' }, stdio: ['ignore', 'pipe', 'pipe'] });
let serverLog = '';
server.stdout.on('data', d => { serverLog += d; }); server.stderr.on('data', d => { serverLog += d; });
let healthy = false;
for (let i = 0; i < 100 && !(healthy = await fetch(`http://127.0.0.1:${mpPort}/healthz`).then(r => r.ok, () => false)); i++) await new Promise(r => setTimeout(r, 100));
if (!healthy) { console.log(`multiplayer server did not start (npm ci --prefix server?):\n${serverLog.slice(-1500)}`); process.exit(2); }

// The bots join first (cheap), then the browsers.
const botFile = path.join(out, 'mp-bots.jsonl'), goFile = path.join(out, 'go');
fs.rmSync(botFile, { force: true }); fs.rmSync(goFile, { force: true });
const bots = B > 0 ? spawn('npx', ['vitest', 'run', '-c', 'vitest.drive.config.ts', 'tests/drive/mpbot.test.ts'], { cwd: path.resolve(import.meta.dirname, '../../game'),
  env: { ...process.env, MP_URL: `ws://127.0.0.1:${mpPort}/`, MP_ORIGIN: new URL(base).origin, DRIVE_DIR: sweepDir, MP_OUT: botFile, MP_BOTS: String(B),
    MP_SECONDS: String(seconds), MP_GO: goFile }, stdio: ['ignore', 'pipe', 'pipe'] }) : null;
let botLog = '';
bots?.stdout.on('data', d => { botLog += d; }); bots?.stderr.on('data', d => { botLog += d; });
const botRows = () => fs.existsSync(botFile) ? fs.readFileSync(botFile, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l)) : [];
if (bots) {
  for (let i = 0; i < 1200 && !botRows().some(r => r.event === 'ready') && bots.exitCode === null; i++) await new Promise(r => setTimeout(r, 500));
  if (!botRows().some(r => r.event === 'ready')) { console.log(`bots did not start:\n${botLog.slice(-2000)}`); bots.kill(); server.kill(); process.exit(2); }
  console.log(new Date().toISOString().slice(11, 19), `${B} bots driving`);
}

const cdp = opt('cdp');
if (!cdp && guard('acquire') === 3) { console.log('browser slots full'); server.kill(); process.exit(3); }
const browser = cdp ? await chromium.connectOverCDP(cdp) : await chromium.launch({ executablePath: chromeExecutable(), headless: true, ignoreDefaultArgs: chromeIgnoreDefaultArgs,
  args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist', '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows']) });
const clients = [];
const samples = [];
const errors = [];
const events = [];

async function join(index) {
  const context = await browser.newContext({ viewport: { width: 480, height: 270 } });
  const page = await context.newPage();
  const client = { index, context, page, nick: null, alive: true };
  page.on('pageerror', e => errors.push({ client: index, t: Date.now(), error: e.message }));
  await page.goto(`${base}?quality=low&telemetry=0&time=13:00&traffic=0&walkers=0&mp=ws://127.0.0.1:${mpPort}/`);
  await page.waitForFunction(() => window.__game?.ready, null, { timeout: 480000 });
  await page.getByRole('option', { name: /Multiplayer/ }).click({ timeout: 60000 });
  await page.waitForFunction(() => { const s = window.__game.state(); return !s.loading && !s.startScreen && s.multiplayer?.connected; }, null, { timeout: 480000 });
  client.nick = await page.evaluate(() => window.__game.state().multiplayer.nick);
  // The own car next to the character (test setup), entered with the interact key like a player.
  await page.evaluate(async i => {
    const g = window.__game, f = g.state().feet;
    const hit = await g.physics.raycast({ x: f[0] + 3, y: f[1] + 4, z: f[2] + 2 * (i % 4) }, { x: 0, y: -1, z: 0 }, 12);
    g.physics.resetVehicle({ x: f[0] + 3, y: (hit?.point.y ?? f[1]) + 0.9, z: f[2] + 2 * (i % 4) }, 0);
  }, index);
  await page.waitForTimeout(800);
  await page.keyboard.press('KeyF');
  await page.waitForFunction(() => window.__game.state().mode === 'car', null, { timeout: 20000 }).catch(() => page.evaluate(() => window.__game.enterVehicle('player')));
  events.push({ t: Date.now(), kind: 'join', client: index, nick: client.nick });
  log(`client ${index} joined as ${client.nick}`);
  clients.push(client);
  return client;
}

/** Four street points around the spawn, snapped to lanes: the out and across targets. */
async function targets(page) {
  return page.evaluate(async () => {
    const g = window.__game, f = g.state().feet, ap = g.autopilot;
    const { tilesBetween } = await import('/src/gameplay/autopilot/lanes.ts');
    const out = [];
    for (const [dx, dz] of [[320, 0], [0, 320], [-320, 0], [0, -320]]) {
      const p = { x: f[0] + dx, z: f[2] + dz };
      await ap.lanes.ensure(tilesBetween({ x: f[0], z: f[2] }, p, 1));
      const near = ap.lanes.near(p, 120, 1)[0];
      if (!near) { out.push(p); continue; }
      const l = near.lane, i = Math.min(l.points.length / 2 - 1, Math.round(l.points.length / 4));
      out.push({ x: l.points[2 * i], z: l.points[2 * i + 1] });
    }
    return out;
  });
}

const drive = (client, target) => client.page.evaluate(t => {
  window.__mpTrip = window.__game.autopilot.goTo(t, { mode: 'drive', takeover: false, speed: 13 });
  window.__mpTrip.done.then(r => { window.__mpDone = r; });
}, target).catch(e => errors.push({ client: client.index, t: Date.now(), error: e.message }));

async function sample(client) {
  if (!client.alive) return;
  const s = await client.page.evaluate(() => {
    const g = window.__game, st = g.state(), v = st.vehicle, mp = st.multiplayer, trip = window.__mpTrip;
    return { wall: Date.now(), mode: st.mode, self: v ? { x: v.pos[0], y: v.pos[1], z: v.pos[2], speed: v.speed } : { x: st.feet[0], y: st.feet[1], z: st.feet[2], speed: 0 },
      impact: v?.impact ?? null, connected: mp?.connected, players: mp?.players, remotes: mp?.remotes ?? [],
      remoteModes: (mp?.remoteVisuals ?? []).map(r => r.mode), frameMs: st.frameMs, remoteFrameMs: mp?.remoteFrameMs,
      trip: trip ? { state: trip.state, reason: trip.reason ?? null, s: trip.progress.s, length: trip.progress.length } : null };
  }).catch(e => ({ error: e.message, wall: Date.now() }));
  samples.push({ client: client.index, nick: client.nick, ...s });
}

let failed = null;
try {
  log(`server on ${mpPort}; joining ${N} clients`);
  // All clients load at once (each loading tab competes with the others' rendering otherwise).
  await Promise.all(Array.from({ length: N }, (_, i) => new Promise(r => setTimeout(r, i * 3000)).then(() => join(i))));
  clients.sort((a, b) => a.index - b.index);
  const goal = await targets(clients[0].page);
  log('targets', JSON.stringify(goal.map(p => [Math.round(p.x), Math.round(p.z)])));
  const t0 = Date.now();
  fs.writeFileSync(goFile, String(t0));
  let phase = 0, left = false, rejoined = false;
  for (const c of clients) { await drive(c, goal[c.index % 4]); await new Promise(r => setTimeout(r, 600)); }
  events.push({ t: Date.now(), kind: 'phase', phase: 1 });
  while (Date.now() - t0 < seconds * 1000) {
    await Promise.all(clients.map(sample));
    const now = Date.now() - t0;
    if (!left && now > seconds * 250 && clients.length > 2) {
      const c = clients.filter(c => c.index < N).at(-1);
      c.alive = false; events.push({ t: Date.now(), kind: 'leave', client: c.index, nick: c.nick });
      log(`client ${c.index} (${c.nick}) leaves mid-drive`);
      await c.context.close(); left = true;
    }
    const tripsDone = clients.filter(c => c.alive).every(c => samples.findLast(s => s.client === c.index)?.trip?.state !== 'driving');
    if (phase === 0 && (now > seconds * 450 || tripsDone)) {
      phase = 1; events.push({ t: Date.now(), kind: 'phase', phase: 2 });
      log('phase 2: across');
      for (const c of clients.filter(c => c.alive)) void drive(c, goal[(c.index + 2) % 4]);
    }
    if (!rejoined && now > seconds * 300) {
      // Joins while the others drive; sampling goes on meanwhile.
      rejoined = true;
      void join(N).then(c => drive(c, goal[(N + 1) % 4])).catch(e => { errors.push({ client: N, t: Date.now(), error: `join: ${e.message}` }); });
    }
    if (!cdp) guard('touch');
    await new Promise(r => setTimeout(r, 250));
  }
} catch (e) { failed = e.message; log('FAILED', e.message); }
finally {
  for (const c of clients) if (c.alive) await c.context.close().catch(() => {});
  if (cdp) { for (const c of browser.contexts()) for (const p of c.pages()) if (p.url().includes(new URL(base).host)) await p.close().catch(() => {}); }
  else { await browser.close(); guard('release'); }
  bots?.kill('SIGTERM');
  server.kill('SIGTERM'); fs.rmSync(scores, { recursive: true, force: true });
}

for (const r of botRows()) {
  if (r.event === 'join' || r.event === 'leave') events.push({ t: r.t, kind: r.event, client: r.client, nick: r.nick, bot: true });
  else if (r.event === 'error') errors.push({ client: 'bots', t: r.t, error: r.error });
  else if (!r.event) samples.push(r);
}
samples.sort((a, b) => a.wall - b.wall);
// Analysis: own trajectories by nick, every remote observation against them.
const own = new Map();
for (const s of samples) if (s.self && !s.error) { const l = own.get(s.nick) ?? []; l.push(s); own.set(s.nick, l); }
const at = (nick, wall) => {
  const l = own.get(nick); if (!l) return null;
  let i = l.findIndex(s => s.wall >= wall);
  if (i <= 0) return i === 0 && l[0].wall - wall < 300 ? l[0].self : null;
  const a = l[i - 1], b = l[i], f = (wall - a.wall) / ((b.wall - a.wall) || 1);
  if (b.wall - a.wall > 1500) return null;
  return { x: a.self.x + (b.self.x - a.self.x) * f, z: a.self.z + (b.self.z - a.self.z) * f, speed: a.self.speed };
};
const pct = (list, p) => { const s = [...list].sort((a, b) => a - b); return s.length ? s[Math.min(s.length - 1, Math.floor(s.length * p))] : null; };
const pairs = [];
for (const s of samples) for (const r of s.remotes ?? []) {
  const truth = at(r.nick, s.wall);
  if (truth) pairs.push({ observer: s.nick, subject: r.nick, wall: s.wall, d: Math.hypot(r.x - truth.x, r.z - truth.z), speed: truth.speed, r });
}
// Latency: the delay that best explains the observations (moving subjects only).
let bestLag = 0, bestErr = Infinity;
for (let lag = 0; lag <= 1500; lag += 10) {
  let sum = 0, n = 0;
  for (const p of pairs) { if (p.speed < 3) continue; const t = at(p.subject, p.wall - lag); if (t) { sum += Math.hypot(p.r.x - t.x, p.r.z - t.z); n++; } }
  if (n && sum / n < bestErr) { bestErr = sum / n; bestLag = lag; }
}
const moving = pairs.filter(p => p.speed >= 3).map(p => p.d);
// Presence: after a leave, how long until every remaining client stopped showing the player; after the join, until all show it.
const presence = events.filter(e => e.kind === 'leave' || e.kind === 'join').map(e => {
  const later = samples.filter(s => s.wall >= e.t && !s.error && s.client !== e.client);
  const shows = s => (s.remotes ?? []).some(r => r.nick === e.nick);
  const byClient = new Map();
  for (const s of later) if (!byClient.has(s.client) && (e.kind === 'leave' ? !shows(s) : shows(s))) byClient.set(s.client, s.wall - e.t);
  return { ...e, observers: [...new Set(later.map(s => s.client))].length, settledMs: byClient.size ? Math.max(...byClient.values()) : null, settled: byClient.size };
});
// Contacts: new impacts on a client's own car while another player's car was within 6 m.
const contacts = [];
const lastImpact = new Map();
for (const s of samples) {
  if (!s.impact) continue;
  const prev = lastImpact.get(s.client); lastImpact.set(s.client, s.impact.id);
  if (prev === undefined || prev === s.impact.id) continue;
  const near = (s.remotes ?? []).map(r => ({ nick: r.nick, d: Math.hypot(r.x - s.self.x, r.z - s.self.z) })).sort((a, b) => a.d - b.d)[0];
  contacts.push({ client: s.client, wall: s.wall, impulse: s.impact.impulse, nearPlayer: near && near.d < 6 ? near : null });
}
const trips = [...new Set(samples.map(s => s.client))].map(c => {
  const l = samples.filter(s => s.client === c && s.trip);
  return { client: c, nick: l[0]?.nick, end: l.at(-1)?.trip ?? null, states: [...new Set(l.map(s => s.trip.state))] };
});
const frames = samples.filter(s => s.frameMs).map(s => s.frameMs);
// Divergence by observer kind: browsers show interpolated remotes (100 ms buffer); bots hold the latest raw pose.
const byObserver = kind => pairs.filter(p => p.speed >= 3 && (samples.find(s => s.nick === p.observer)?.bot ? 'bot' : 'browser') === kind).map(p => p.d);
const result = { browsers: N + 1, bots: B + 1, seconds,
  divergenceByObserver: Object.fromEntries(['browser', 'bot'].map(k => { const l = byObserver(k); return [k, { n: l.length, p50: pct(l, 0.5), p95: pct(l, 0.95), max: pct(l, 1) }]; })), failed, samples: samples.length,
  divergence: { moving: { n: moving.length, p50: pct(moving, 0.5), p95: pct(moving, 0.95), max: pct(moving, 1) }, all: { n: pairs.length, p95: pct(pairs.map(p => p.d), 0.95) } },
  latency: { bestLagMs: bestLag, residualM: bestErr }, presence, contacts: { total: contacts.length, nearPlayer: contacts.filter(c => c.nearPlayer).length, list: contacts.slice(0, 30) },
  trips, frameMs: { p50: pct(frames, 0.5), p95: pct(frames, 0.95) }, errors: errors.slice(0, 30), serverLog: serverLog.slice(-2000) };
fs.writeFileSync(path.join(out, 'mp-result.json'), JSON.stringify(result, null, 1));
fs.writeFileSync(path.join(out, 'mp-samples.jsonl'), samples.map(s => JSON.stringify(s)).join('\n'));
console.log(JSON.stringify({ ...result, contacts: { total: result.contacts.total, nearPlayer: result.contacts.nearPlayer }, serverLog: undefined, errors: errors.length }, null, 1));
