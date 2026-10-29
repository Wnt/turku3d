// Owned headed Chrome, real Pages build, normal caching and repeatable cold flights.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn, execFileSync } from 'node:child_process';
import { chromium } from '../../../game/node_modules/playwright-core/index.mjs';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot, port } from '../../dev/environment.mjs';
import { installFlight } from './runtime.mjs';

isolateBrowser();
const label = process.argv[2];
if (!/^[a-z0-9-]+$/.test(label ?? '')) throw new Error('Usage: run.mjs ITERATION_LABEL');
const directions = {
  south: { dx: 0, dz: 1, axis: 2, sign: 1, edge: 500, targets: ['t0_0_2', 't0_0_3'], cold: ['t0_-1_2', 't0_0_2', 't0_1_2', 't0_0_3'] },
  west: { dx: -1, dz: 0, axis: 0, sign: -1, edge: -250, targets: ['t0_-2_1', 't0_-3_1'], cold: ['t0_-2_0', 't0_-2_1', 't0_-2_2', 't0_-3_1'] },
  north: { dx: 0, dz: -1, axis: 2, sign: -1, edge: 0, targets: ['t0_0_-1', 't0_0_-2'], cold: ['t0_-1_-1', 't0_0_-1', 't0_1_-1', 't0_0_-2'] },
  east: { dx: 1, dz: 0, axis: 0, sign: 1, edge: 500, targets: ['t0_2_1', 't0_3_1'], cold: ['t0_2_0', 't0_2_1', 't0_2_2', 't0_3_1'] },
};
const direction = process.argv[3] ?? 'south', route = directions[direction];
if (!route) throw new Error('Direction must be south, west, north or east');
const output = path.join(reportsRoot, 'flight-loop', label);
const profile = path.join(process.env.TURKU3D_BROWSER_DIR, label);
if (fs.existsSync(profile) || fs.existsSync(output)) throw new Error('Use a new label for an empty profile and unambiguous evidence');
fs.mkdirSync(output, { recursive: true });
const log = (file, row) => fs.appendFileSync(path.join(output, file), JSON.stringify({ at: Date.now(), ...row }) + '\n');
const ssh = ['-i', path.join(os.homedir(), '.ssh/oci_cuttlefish'), '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=8', 'ubuntu@82.70.50.61'];
const host = fs.readFileSync(new URL('./host.py', import.meta.url), 'utf8');
const url = 'https://wnt.github.io/turku-game/game/?dev=1';
const context = await chromium.launchPersistentContext(profile, {
  executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs, headless: false,
  viewport: null, args: chromeArgs(['--window-size=1280,900', '--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist', `--remote-debugging-port=${port(3)}`]),
});
let monitor, healthTimer, trace = false, session;
const started = Date.now();
const deadline = setTimeout(() => void context.close(), 600000);
try {
  const page = context.pages()[0];
  session = await context.newCDPSession(page);
  await session.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await session.send('Network.enable');
  const requests = new Map();
  session.on('Network.requestWillBeSent', e => {
    requests.set(e.requestId, { at: e.wallTime * 1000, monotonic: e.timestamp, url: e.request.url });
    log('network.jsonl', { kind: 'request', id: e.requestId, wall: e.wallTime * 1000, monotonic: e.timestamp, url: e.request.url, type: e.type });
  });
  session.on('Network.responseReceived', e => {
    const r = e.response, row = requests.get(e.requestId);
    log('network.jsonl', { kind: 'response', id: e.requestId, url: r.url, status: r.status, monotonic: e.timestamp, ms: row ? (e.timestamp - row.monotonic) * 1000 : null,
      sw: r.fromServiceWorker, source: r.serviceWorkerResponseSource, disk: r.fromDiskCache, protocol: r.protocol,
      timing: r.timing, cache: r.headers['cache-control'] ?? r.headers['Cache-Control'],
      storedBytes: r.headers['x-turku3d-cache-bytes'] ?? r.headers['X-Turku3D-Cache-Bytes'] });
  });
  session.on('Network.loadingFinished', e => { const r = requests.get(e.requestId); log('network.jsonl', { kind: 'finished', id: e.requestId, url: r?.url, monotonic: e.timestamp, ms: r ? (e.timestamp - r.monotonic) * 1000 : null, bytes: e.encodedDataLength }); });
  session.on('Network.loadingFailed', e => log('network.jsonl', { kind: 'failed', id: e.requestId, url: requests.get(e.requestId)?.url, error: e.errorText }));
  page.on('console', m => log('console.jsonl', { kind: m.type(), text: m.text() }));
  page.on('pageerror', e => log('console.jsonl', { kind: 'exception', text: e.message }));
  await page.addInitScript(() => {
    window.__longTasks = [];
    window.__resources = [];
    window.__resizes = [];
    addEventListener('resize', () => window.__resizes.push({ at: Date.now(), width: innerWidth, height: innerHeight }));
    performance.setResourceTimingBufferSize(20000);
    new PerformanceObserver(list => window.__resources.push(...list.getEntries().map(e => ({ ...e.toJSON(), at: performance.timeOrigin + e.startTime })))).observe({ type: 'resource', buffered: true });
    new PerformanceObserver(list => window.__longTasks.push(...list.getEntries().map(e => ({ at: performance.timeOrigin + e.startTime, ms: e.duration })))).observe({ type: 'longtask', buffered: true });
  });
  await session.send('Tracing.start', { categories: 'devtools.timeline,v8,blink.user_timing,disabled-by-default-devtools.timeline', transferMode: 'ReturnAsStream' }); trace = true;
  const cold = execFileSync('ssh', [...ssh, `python3 - cold ${label} ${route.cold.join(',')}`], { input: host, encoding: 'utf8', timeout: 15000 });
  fs.writeFileSync(path.join(output, 'cold.jsonl'), cold);
  const hostLog = fs.openSync(path.join(output, 'host.jsonl'), 'w');
  monitor = spawn('ssh', [...ssh, `python3 -u - monitor 600 ${route.cold.join(',')}`], { stdio: ['pipe', hostLog, hostLog] });
  monitor.stdin.end(host); fs.closeSync(hostLog);
  let healthBusy = false;
  healthTimer = setInterval(async () => {
    if (healthBusy) return; healthBusy = true;
    await Promise.all(['coarse/health', 'healthz'].map(async route => {
      const at = performance.now();
      try { const response = await fetch(`https://turku.lab.madekivi.fi/${route}`, { signal: AbortSignal.timeout(3000) }); log('public.jsonl', { route, ms: performance.now() - at, body: await response.json() }); }
      catch (error) { log('public.jsonl', { route, ms: performance.now() - at, error: error.message }); }
    })); healthBusy = false;
  }, 3000);
  console.log(JSON.stringify({ stage: 'cold-navigation', label, output }));
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForFunction(() => window.__game?.ready || window.__qaError, null, { timeout: 240000 });
  const error = await page.evaluate(() => window.__qaError); if (error) throw new Error(error);
  const multiplayer = page.locator('[data-option="multiplayer"]');
  if (await multiplayer.isVisible()) await multiplayer.click();
  else {
    await page.locator('[data-option="new"]').click();
    await page.getByRole('button', { name: 'Skip', exact: true }).click();
  }
  await page.waitForFunction(() => !window.__game.state().startScreen && !window.__game.state().loading, null, { timeout: 60000 });
  console.log(JSON.stringify({ stage: 'flight', installed: await page.evaluate(installFlight, route) }));
  let shot = 0;
  const dimensions = () => ({ width: innerWidth, height: innerHeight, dpr: devicePixelRatio, resizes: window.__resizes.length,
    canvases: [...document.querySelectorAll('canvas')].map(c => ({ width: c.width, height: c.height, clientWidth: c.clientWidth, clientHeight: c.clientHeight })) });
  while (Date.now() - started < 540000) {
    const state = await page.evaluate(() => ({ phase: window.__flight.phase, done: window.__flight.done, last: window.__flight.samples.at(-1) }));
    log('progress.jsonl', state);
    const before = await page.evaluate(dimensions);
    const capture = await session.send('Page.captureScreenshot', { format: 'jpeg', quality: 75, fromSurface: true });
    fs.writeFileSync(path.join(output, `screen-${String(shot++).padStart(3, '0')}.jpg`), Buffer.from(capture.data, 'base64'));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
    const after = await page.evaluate(dimensions);
    log('screenshots.jsonl', { shot: shot - 1, before, after });
    if (JSON.stringify(before) !== JSON.stringify(after)) throw new Error('Window or renderer dimensions changed during screenshot capture');
    if (state.done) break;
    await page.waitForTimeout(4000);
  }
  const result = await page.evaluate(() => ({ ...window.__flight, longTasks: window.__longTasks, state: window.__game.state(), backend: window.__game.backend,
    resources: window.__resources, resizes: window.__resizes }));
  result.direction = direction; result.route = route;
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result));
  console.log(JSON.stringify({ stage: 'complete', done: result.done, phase: result.phase, seconds: (Date.now() - started) / 1000, output }));
  if (!result.done) throw new Error('Flight did not complete before deadline');
} finally {
  clearTimeout(deadline); clearInterval(healthTimer);
  if (trace) {
    const completed = new Promise(resolve => session.once('Tracing.tracingComplete', resolve));
    await session.send('Tracing.end').catch(() => {});
    const result = await Promise.race([completed, new Promise(resolve => setTimeout(() => resolve(null), 10000))]);
    if (result?.stream) {
      const fd = fs.openSync(path.join(output, 'trace.json'), 'w');
      try { for (;;) { const r = await session.send('IO.read', { handle: result.stream }); fs.writeSync(fd, r.base64Encoded ? Buffer.from(r.data, 'base64') : r.data); if (r.eof) break; } }
      finally { fs.closeSync(fd); await session.send('IO.close', { handle: result.stream }); }
    }
  }
  monitor?.kill('SIGTERM');
  try { fs.writeFileSync(path.join(output, 'multiplayer.log'), execFileSync('ssh', [...ssh, `journalctl --user -u turku-mp-server --since '@${Math.floor(started / 1000)}' --no-pager -n 100`], { timeout: 10000 })); } catch (error) { log('console.jsonl', { kind: 'journal', text: error.message }); }
  await context.close();
}
