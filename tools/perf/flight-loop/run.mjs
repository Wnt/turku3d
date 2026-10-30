// Owned headed Chrome, real Pages build, normal caching and repeatable cold flights.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn, execFileSync } from 'node:child_process';
import { chromium } from '../../../game/node_modules/playwright-core/index.mjs';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot, port } from '../../dev/environment.mjs';
import { installFlight } from './runtime.mjs';
import { installPreparation } from './preparation.mjs';
import { PLACES } from '../../../game/src/gameplay/content/places.ts';
import { enToEngine, VISUAL_L0 } from '../../../game/src/core/origin.ts';

isolateBrowser();
const label = process.argv[2];
if (!/^[a-z0-9-]+$/.test(label ?? '')) throw new Error('Usage: run.mjs ITERATION_LABEL');
const directions = {
  south: { dx: 0, dz: 1, axis: 2, sign: 1, edge: 500, targets: ['t0_0_2', 't0_0_3'], cold: ['t0_-1_2', 't0_0_2', 't0_1_2', 't0_0_3'] },
  west: { dx: -1, dz: 0, axis: 0, sign: -1, edge: -250, targets: ['t0_-2_1', 't0_-3_1'], cold: ['t0_-2_0', 't0_-2_1', 't0_-2_2', 't0_-3_1'] },
  north: { dx: 0, dz: -1, axis: 2, sign: -1, edge: 0, targets: ['t0_0_-1', 't0_0_-2'], cold: ['t0_-1_-1', 't0_0_-1', 't0_1_-1', 't0_0_-2'] },
  east: { dx: 1, dz: 0, axis: 0, sign: 1, edge: 500, targets: ['t0_2_1', 't0_3_1'], cold: ['t0_2_0', 't0_2_1', 't0_2_2', 't0_3_1'] },
};
const direction = process.argv[3] ?? 'south';
let route = directions[direction];
if (!route) throw new Error('Direction must be south, west, north or east');
const placeId = process.argv[4];
const place = placeId && PLACES.find(p => p.id === placeId);
if (placeId && !place) throw new Error(`Unknown starting place: ${placeId}`);
if (place) {
  const { x, z } = enToEngine(place.E, place.N);
  const i = Math.floor(x / VISUAL_L0), j = Math.floor(z / VISUAL_L0);
  const { dx, dz, axis, sign } = route;
  const tile = (along, across = 0) => `t0_${i + dx * along + dz * across}_${j + dz * along + dx * across}`;
  route = { dx, dz, axis, sign, startPlace: place.id, startTile: tile(0),
    edge: ((axis === 0 ? i : j) + (sign > 0 ? 2 : -1)) * VISUAL_L0,
    targets: [tile(2), tile(3)], cold: [tile(2, -1), tile(2), tile(2, 1), tile(3)] };
}
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
let monitor, healthTimer, trace = false, session, traceSession;
const page = context.pages()[0];
const saveResult = async interrupted => {
  const result = await page.evaluate(() => window.__flight && ({ ...window.__flight, longTasks: window.__longTasks, state: window.__game.state(), backend: window.__game.backend,
    resources: window.__resources, resizes: window.__resizes, captures: window.__captures,
    preparation: window.__preparation?.snapshot() }));
  if (!result) return;
  Object.assign(result, { direction, route, ...(interrupted ? { interrupted } : {}) });
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result));
  return result;
};
const started = Date.now();
const deadline = setTimeout(() => void context.close(), 600000);
try {
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
    window.__captures = [];
    addEventListener('resize', () => window.__resizes.push({ at: Date.now(), width: innerWidth, height: innerHeight }));
    performance.setResourceTimingBufferSize(20000);
    new PerformanceObserver(list => window.__resources.push(...list.getEntries().map(e => ({ ...e.toJSON(), at: performance.timeOrigin + e.startTime })))).observe({ type: 'resource', buffered: true });
    new PerformanceObserver(list => window.__longTasks.push(...list.getEntries().map(e => ({ at: performance.timeOrigin + e.startTime, ms: e.duration })))).observe({ type: 'longtask', buffered: true });
  });
  traceSession = await context.browser().newBrowserCDPSession();
  await traceSession.send('Tracing.start', { categories: 'devtools.timeline,v8,blink.user_timing', transferMode: 'ReturnAsStream', streamCompression: 'gzip' }); trace = true;
  const cold = execFileSync('ssh', [...ssh, `python3 - cold ${label} ${route.cold.join(',')}`], { input: host, encoding: 'utf8', timeout: 135000 });
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
  if (place) {
    const relocationAt = Date.now();
    await page.keyboard.press('Escape');
    await page.locator('[data-option="dev"]').click();
    await page.locator('[data-option="teleport"]').click();
    await page.locator(`[data-option="place-${place.id}"]`).click();
    await page.waitForFunction(({ E, N }) => {
      const s = window.__game.state();
      return !s.loading && Math.hypot(s.E - E, s.N - N) < 2;
    }, place, { timeout: 60000 });
    await page.waitForFunction(id => {
      const e = window.__game.coarse.entries.get(id);
      return !!e?.model && e.collision.size === 16;
    }, route.startTile, { timeout: 120000 });
    log('setup.jsonl', { kind: 'relocated', place: place.id, ms: Date.now() - relocationAt,
      state: await page.evaluate(() => window.__game.state()) });
  }
  log('setup.jsonl', { kind: 'preparation-hooks', capabilities: await page.evaluate(installPreparation) });
  console.log(JSON.stringify({ stage: 'flight', installed: await page.evaluate(installFlight, route) }));
  let shot = 0;
  const dimensions = () => ({ width: innerWidth, height: innerHeight, dpr: devicePixelRatio, resizes: window.__resizes.length,
    canvases: [...document.querySelectorAll('canvas')].map(c => ({ width: c.width, height: c.height, clientWidth: c.clientWidth, clientHeight: c.clientHeight })) });
  while (Date.now() - started < 540000) {
    const stop = path.join(output, 'stop-request.json');
    if (fs.existsSync(stop)) {
      const { reason = 'Flight stopped by its owner' } = JSON.parse(fs.readFileSync(stop, 'utf8'));
      throw new Error(reason);
    }
    const state = await page.evaluate(() => ({ phase: window.__flight.phase, done: window.__flight.done, last: window.__flight.samples.at(-1) }));
    log('progress.jsonl', state);
    await page.evaluate(() => {
      // Hold only the adaptive-resolution decision during capture. Otherwise its
      // normal scale probe can resize the drawing buffer between the two checks.
      const resolution = window.__game.resolution;
      window.__captureResolutionUpdate = resolution.update;
      resolution.update = () => resolution.scale;
      window.__captures.push({ at: performance.timeOrigin + performance.now() });
    });
    try {
      const before = await page.evaluate(dimensions);
      const capture = await session.send('Page.captureScreenshot', { format: 'jpeg', quality: 75, fromSurface: true });
      fs.writeFileSync(path.join(output, `screen-${String(shot++).padStart(3, '0')}.jpg`), Buffer.from(capture.data, 'base64'));
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
      const after = await page.evaluate(dimensions);
      log('screenshots.jsonl', { shot: shot - 1, before, after });
      if (JSON.stringify(before) !== JSON.stringify(after)) throw new Error('Window or renderer dimensions changed during screenshot capture');
    } finally {
      await page.evaluate(() => {
        window.__game.resolution.update = window.__captureResolutionUpdate;
        window.__captures.at(-1).end = performance.timeOrigin + performance.now();
        delete window.__captureResolutionUpdate;
      });
    }
    if (state.done) break;
    await page.waitForTimeout(4000);
  }
  const result = await saveResult();
  await page.evaluate(() => window.__preparation?.dispose());
  // After flight timings end, separate transient allocations from retained main-thread heap.
  const beforeGc = await page.evaluate(() => performance.memory?.usedJSHeapSize);
  const beforeUsage = await session.send('Runtime.getHeapUsage');
  await session.send('HeapProfiler.collectGarbage');
  const afterGc = await page.evaluate(() => performance.memory?.usedJSHeapSize);
  const afterUsage = await session.send('Runtime.getHeapUsage');
  result.heapAfterFlight = { beforeGc, afterGc };
  result.heapUsageAfterFlight = { beforeGc: beforeUsage, afterGc: afterUsage };
  result.preparation = await page.evaluate(() => {
    window.__preparation?.sampleMemory('post-gc');
    return window.__preparation?.snapshot({ memory: false });
  });
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result));
  console.log(JSON.stringify({ stage: 'complete', done: result.done, phase: result.phase, seconds: (Date.now() - started) / 1000, output }));
  if (!result.done) throw new Error('Flight did not complete before deadline');
} catch (error) {
  await saveResult(error.message).catch(() => {});
  throw error;
} finally {
  clearTimeout(deadline); clearInterval(healthTimer);
  if (trace) {
    const completed = new Promise(resolve => traceSession.once('Tracing.tracingComplete', resolve));
    await traceSession.send('Tracing.end').catch(error => log('trace-status.jsonl', { error: error.message }));
    let traceTimeout;
    const result = await Promise.race([completed, new Promise(resolve => { traceTimeout = setTimeout(() => resolve(null), 30000); })]);
    clearTimeout(traceTimeout);
    log('trace-status.jsonl', { complete: !!result?.stream, dataLossOccurred: result?.dataLossOccurred });
    if (result?.stream) {
      const fd = fs.openSync(path.join(output, 'trace.json.gz'), 'w');
      try { for (;;) { const r = await traceSession.send('IO.read', { handle: result.stream }); fs.writeSync(fd, r.base64Encoded ? Buffer.from(r.data, 'base64') : r.data); if (r.eof) break; } }
      finally { fs.closeSync(fd); await traceSession.send('IO.close', { handle: result.stream }); }
    }
  }
  monitor?.kill('SIGTERM');
  try { fs.writeFileSync(path.join(output, 'multiplayer.log'), execFileSync('ssh', [...ssh, `journalctl --user -u turku-mp-server --since '@${Math.floor(started / 1000)}' --no-pager -n 100`], { timeout: 10000 })); } catch (error) { log('console.jsonl', { kind: 'journal', text: error.message }); }
  await context.close();
}
