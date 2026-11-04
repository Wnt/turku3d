// Browser pass of the drive sweep: in the real game (headless Chrome, WebGPU), drive selected lanes with the
// autopilot through the normal input path and trace them from the physics snapshots, then put the car just before
// the worst point and take a chase-camera screenshot there.
//
//   node tools/drivetest/browser.mjs DIR --base http://127.0.0.1:5480/ [--keys k1,k2|--failures N|--sample N] [--no-drive]
//
// Uses one browser through chrome-guard (NAME autopilot). Writes DIR/shots/<key>.png and DIR/browser.jsonl.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { chromium } from '../../game/node_modules/@playwright/test/index.mjs';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../dev/chrome-args.mjs';

const args = process.argv.slice(2);
const dir = args[0];
const opt = name => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : undefined; };
const base = opt('base') ?? 'http://127.0.0.1:5480/';
// Chrome leases on the shared hosts (~/turku3d/tools/host-lease/lease.py): acquire 0 granted / 3 queued, touch 2 = pause.
const LEASE_HOST = process.env.LEASE_HOST ?? 'mac';
const guard = cmd => { try { execFileSync('python3', [path.join(process.env.HOME, 'turku3d/tools/host-lease/lease.py'), cmd === 'acquire' ? 'acquire' : cmd, ...(cmd === 'acquire' ? [LEASE_HOST, 'autopilot', '--kind', 'func', '--ttl', '40m'] : ['autopilot', LEASE_HOST])], { stdio: 'ignore' }); return 0; } catch (e) { return e.status ?? 1; } };

const rows = [];
for (const f of fs.readdirSync(path.join(dir, 'results')).filter(f => f.endsWith('.jsonl')))
  for (const line of fs.readFileSync(path.join(dir, 'results', f), 'utf8').split('\n')) if (line) rows.push(JSON.parse(line));
let pick;
if (opt('keys')) pick = rows.filter(r => opt('keys').split(',').includes(r.key));
else if (opt('sample')) { const n = Number(opt('sample')); const step = Math.max(1, Math.floor(rows.length / n)); pick = rows.filter((_, i) => i % step === 0).slice(0, n); }
else pick = rows.filter(r => r.severity === 'fail' && r.view).sort((a, b) => (b.worst?.value ?? 0) - (a.worst?.value ?? 0)).slice(0, Number(opt('failures') ?? 20));
const drive = !args.includes('--no-drive');
fs.mkdirSync(path.join(dir, 'shots'), { recursive: true });
const out = path.join(dir, 'browser.jsonl');

for (let i = 0; guard('acquire') === 3; i++) { if (i > 60) { console.log('no browser lease for 30 min'); process.exit(3); } await new Promise(r => setTimeout(r, 30000)); }
const ctx = await chromium.launchPersistentContext(path.join(process.env.HOME, 'turku3d/loops/autopilot/browser/profile'), {
  executablePath: chromeExecutable(), headless: true, ignoreDefaultArgs: chromeIgnoreDefaultArgs,
  args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']), viewport: { width: 1280, height: 720 } });
try {
  const page = ctx.pages()[0] ?? await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(`${base}?quality=medium&telemetry=0&time=13:00&traffic=0&walkers=0&mp=ws://127.0.0.1:1`);
  await page.waitForFunction(() => window.__game?.ready, null, { timeout: 180000 });
  await page.getByRole('option', { name: 'New game', exact: true }).click();
  await page.getByRole('button', { name: 'Skip', exact: true }).click().catch(() => {});
  await page.waitForFunction(() => !window.__game.state().loading && !window.__game.state().startScreen, null, { timeout: 120000 });
  await page.evaluate(() => window.__game.enterVehicle('player'));
  // The in-page driver: place the car at the lane path's start (test setup), autopilot.followLane, trace snapshots.
  await page.evaluate(async () => {
    const { DriveTrace } = await import('/src/qa/drivetest/trace.ts');
    const { sampleAt } = await import('/src/gameplay/autopilot/path.ts');
    const { tilesBetween } = await import('/src/gameplay/autopilot/lanes.ts');
    const g = window.__game;
    const settle = async (x, z, ms = 90000) => {
      const t0 = performance.now();
      while (performance.now() - t0 < ms) {
        const p = g.coarse?.progressAt(x, z);
        const cell = `c_${Math.floor(x / 62.5)}_${Math.floor(z / 62.5)}`;
        if ((!p || p.loaded === p.total) && g.physics.loaded.has(cell)) return true;
        await new Promise(r => setTimeout(r, 250));
      }
      return false;
    };
    const place = async (x, z, hx, hz, y) => {
      await settle(x, z);
      const hit = await g.physics.raycast({ x, y: (y ?? 50) + 3, z }, { x: 0, y: -1, z: 0 }, 12);
      g.physics.resetVehicle({ x, y: (hit?.point.y ?? y ?? 0) + 0.9, z }, Math.atan2(-hz, hx), 'player');
      await new Promise(r => setTimeout(r, 1200));
    };
    window.__drivetest = {
      async lane(key, window_, doDrive) {
        const [, laneKey] = key.split('|'), [tile, id] = laneKey.split(':');
        const ap = g.autopilot;
        const at = /^t0_(-?\d+)_(-?\d+)$/.exec(tile);
        const cx = (+at[1] + 0.5) * 250, cz = (+at[2] + 0.5) * 250;
        await ap.lanes.ensure(tilesBetween({ x: cx, z: cz }, { x: cx, z: cz }, 1));
        const route = ap.lanes.follow({ tile, id }, 90, 50);
        if (!route) return { error: 'unknown lane' };
        const start = sampleAt(route.path, 0);
        await place(start.x, start.z, start.hx, start.hz, start.y);
        if (!doDrive) return { route: route.path.length };
        const trace = new DriveTrace(window_);
        const trip = ap.followLane({ tile, id }, { takeover: false, speed: 22 });
        let lastTick = -1, frames = [], last = performance.now();
        await new Promise(resolve => {
          const tick = () => {
            const now = performance.now(); frames.push(now - last); last = now;
            const st = g.physics.latest, v = st?.vehicles.find(v => v.id === 'player');
            if (v && st.tick !== lastTick) {
              const dt = lastTick < 0 ? 1 / 60 : (st.tick - lastTick) / 60;
              lastTick = st.tick;
              const s = trip.progress.s;
              trace.step(v, dt, s, sampleAt(trip.path ?? route.path, s).y);
            }
            if (trip.state === 'driving' || trip.state === 'planning') requestAnimationFrame(tick); else resolve();
          };
          requestAnimationFrame(tick);
        });
        trace.frameMs = frames;
        const result = await trip.done;
        return { state: result.state, reason: result.reason, summary: trace.summary(), progress: result.progress };
      },
      async view(v) {
        // 18 m before the worst point, facing it; the chase camera follows the car.
        await place(v.x - v.hx * 18, v.z - v.hz * 18, v.hx, v.hz, v.y);
        await new Promise(r => setTimeout(r, 2500));
      },
    };
  });
  for (const r of pick) {
    guard('touch');
    const t0 = Date.now();
    const result = await page.evaluate(([key, w, d]) => window.__drivetest.lane(key, w, d), [r.key, r.window, drive]).catch(e => ({ error: e.message }));
    let shot = null;
    if (r.view) {
      await page.evaluate(v => window.__drivetest.view(v), r.view);
      shot = `${r.key.replace(/[^\w-]+/g, '_')}.png`;
      await page.screenshot({ path: path.join(dir, 'shots', shot) });
    }
    const row = { key: r.key, node: { severity: r.severity, worst: r.worst?.kind, value: r.worst?.value, peaks: r.peaks },
      browser: result.summary ? { state: result.state, reason: result.reason, severity: result.summary.severity, worst: result.summary.worst?.kind,
        value: result.summary.worst?.value, peaks: result.summary.peaks, frameP95: result.summary.frameP95 } : result, shot, ms: Date.now() - t0, errors: errors.splice(0) };
    fs.appendFileSync(out, JSON.stringify(row) + '\n');
    console.log(`${r.name || r.structure} ${r.heading}°: node ${r.severity}/${r.worst?.kind ?? '-'} browser ${row.browser.severity ?? row.browser.error}/${row.browser.worst ?? '-'} ${row.ms} ms`);
  }
} finally {
  await ctx.close();
  guard('release');
}
