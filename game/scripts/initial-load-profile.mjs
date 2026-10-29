// Deployed cold/warm startup evidence. Run under host_slots.py browser; no local server or synthetic tiles.
import fs from 'node:fs';
import { installScenario } from '../../tools/perf/air-scenarios/runtime.mjs';
import { installStreamingProfile } from './streaming-profile.mjs';
import assert from 'node:assert/strict';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import WebSocket from '../../server/node_modules/ws/wrapper.mjs';
import { createServer } from '../../server/http.js';
import { ScoreStore } from '../../server/store.js';
import { isolateBrowser, reportsRoot, port } from '../../tools/dev/environment.mjs';

isolateBrowser();
const [label = 'current', url = 'https://wnt.github.io/turku-game/game/', requirement = 'bundled'] = process.argv.slice(2);
const mobile = process.env.TURKU3D_PROFILE_MOBILE === '1';
const diagnostic = process.env.TURKU3D_PROFILE_TRACE !== '0';
const seconds = Number(process.env.TURKU3D_PROFILE_SECONDS ?? 30);
const strict = process.env.TURKU3D_PROFILE_STRICT === '1';
const checks = [];
const drive = process.env.TURKU3D_PROFILE_DRIVE === '1';
const out = path.join(reportsRoot, 'initial-load', label);
fs.mkdirSync(out, { recursive: true });
// Pages upload completion precedes CDN publication. Wait outside Chrome, inside the async wall budget.
if (requirement === 'bundled') {
  const deadline = Date.now() + 120_000;
  for (;;) {
    const html = await fetch(`${url}?startup-publication=${Date.now()}`, { cache: 'no-store', signal: AbortSignal.timeout(5000) }).then(r => r.text());
    let current = html.includes('rel="modulepreload"') && html.includes('City loading progress');
    if (current && (process.env.TURKU3D_PROFILE_WORKER === '1' || process.env.TURKU3D_PROFILE_EDGE_WARMUP === '1')) {
      const entry = html.match(/href="([^"]*assets\/main-[^"]+\.js)"/);
      const source = entry ? await fetch(new URL(entry[1], url)).then(r => r.text()) : '';
      if (process.env.TURKU3D_PROFILE_EDGE_WARMUP === '1') current &&= source.includes('edge-material-warmup');
      const worker = source.match(/decodeGlb\.worker-[A-Za-z0-9_-]+\.js/);
      if (process.env.TURKU3D_PROFILE_WORKER === '1') current &&= !!worker && (await fetch(new URL(`assets/${worker[0]}`, url)).then(r => r.text())).includes('32768');
    }
    if (current) break;
    if (Date.now() >= deadline) throw new Error('Pages still serves the previous startup implementation');
    await new Promise(resolve => setTimeout(resolve, 5000));
  }
}
const context = await chromium.launchPersistentContext(path.join(process.env.TURKU3D_BROWSER_DIR, `initial-load-${label}`), {
  executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs, headless: true,
  viewport: mobile ? { width: 844, height: 390 } : { width: 2560, height: 1440 },
  hasTouch: mobile, isMobile: mobile, deviceScaleFactor: mobile ? 3 : 1, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']),
});
let room;
const hardStop = setTimeout(() => { void context.close(); }, 440_000);
try {
  for (const cache of (process.env.TURKU3D_PROFILE_CACHES ?? 'cold,warm').split(',')) {
    room = createServer({ store: new ScoreStore(path.join(out, `${cache}-scores`)) });
    await new Promise((resolve, reject) => { room.server.once('error', reject); room.server.listen(port(4), '127.0.0.1', resolve); });
    const runUrl = new URL(url); runUrl.searchParams.set('mp', 'wss://first-play.invalid/');
    const page = await context.newPage(), cdp = await context.newCDPSession(page);
    if (mobile) {
      await cdp.send('Emulation.setCPUThrottlingRate', { rate: 6 });
    }
    // Route through Playwright's host transport: HTTPS Pages cannot open insecure loopback WebSockets.
    await page.routeWebSocket('wss://first-play.invalid/', socket => {
      const upstream = new WebSocket(`ws://127.0.0.1:${port(4)}/`, { origin: new URL(url).origin });
      const queued = [];
      socket.onMessage(message => { if (upstream.readyState === WebSocket.OPEN) upstream.send(message); else queued.push(message); });
      upstream.on('open', () => { for (const message of queued) upstream.send(message); queued.length = 0; });
      upstream.on('message', (data, binary) => socket.send(binary ? data : data.toString()));
      upstream.on('error', error => { console.error(error.message); socket.close(); });
      upstream.on('close', () => socket.close());
      socket.onClose(() => upstream.close());
    });
    await page.route('https://first-play.invalid/**', async route => {
      const request = new URL(route.request().url());
      const response = await route.fetch({ url: `http://127.0.0.1:${port(4)}${request.pathname}${request.search}` });
      await route.fulfill({ response });
    });
    await cdp.send('Network.enable');
    if (cache === 'cold') await cdp.send('Network.clearBrowserCache');
    await cdp.send('Performance.enable');
    await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
    const requests = new Map(), errors = [];
    cdp.on('Network.requestWillBeSent', e => requests.set(e.requestId, { url: e.request.url, start: e.timestamp, type: e.type }));
    cdp.on('Network.responseReceived', e => Object.assign(requests.get(e.requestId) ?? {}, {
      status: e.response.status, protocol: e.response.protocol, timing: e.response.timing,
      diskCache: e.response.fromDiskCache, serviceWorker: e.response.fromServiceWorker,
      encoding: e.response.headers['content-encoding'], cacheControl: e.response.headers['cache-control'],
    }));
    cdp.on('Network.loadingFinished', e => Object.assign(requests.get(e.requestId) ?? {}, { end: e.timestamp, bytes: e.encodedDataLength }));
    cdp.on('Network.loadingFailed', e => Object.assign(requests.get(e.requestId) ?? {}, { error: e.errorText }));
    page.on('pageerror', e => { if (!errors.includes(e.message)) errors.push(e.message); });
    if (diagnostic) await page.addInitScript(installStreamingProfile);
    await page.addInitScript(({ seconds }) => {
      localStorage.clear(); sessionStorage.clear();
      performance.setResourceTimingBufferSize(10000);
      const p = window.__initialLoad = { ready: null, click: null, firstFrame: null, frames: [], frameTimes: [], tasks: [], shifts: [], log: [], stages: [], bounds: [] };
      for (const type of ['longtask', 'layout-shift']) new PerformanceObserver(list => {
        for (const e of list.getEntries()) (type === 'longtask' ? p.tasks : p.shifts).push(e.toJSON());
      }).observe({ type, buffered: true });
      let prev = 0, log = '', stage = '', bounds = '';
      const frame = now => {
        const el = document.querySelector('.loading-log'), active = document.querySelector('.loading-active');
        if (el?.textContent !== log) { log = el?.textContent; p.log.push({ at: now, text: log }); }
        if (active?.textContent !== stage) { stage = active?.textContent; p.stages.push({ at: now, text: stage }); }
        const rect = document.querySelector('.loading-panel')?.getBoundingClientRect();
        const nextBounds = rect && JSON.stringify([rect.x, rect.y, rect.width, rect.height]);
        if (nextBounds !== bounds) { bounds = nextBounds; p.bounds.push({ at: now, rect: nextBounds }); }
        if (window.__game?.ready && p.ready === null) p.ready = now;
        const state = window.__game?.state?.();
        if (p.click !== null && state && !state.startScreen && !state.loading && !state.paused) {
          if (p.firstFrame === null) p.firstFrame = now;
          if (prev) { p.frames.push(now - prev); p.frameTimes.push(now); }
          prev = now;
        }
        if (p.firstFrame === null || now - p.firstFrame < seconds * 1000) requestAnimationFrame(frame);
        else p.done = true;
      };
      requestAnimationFrame(frame);
    }, { seconds });
    if (diagnostic) {
      await cdp.send('Profiler.enable');
      await cdp.send('Profiler.setSamplingInterval', { interval: 1000 });
      await cdp.send('Profiler.start');
    }
    if (diagnostic) await cdp.send('Tracing.start', { categories: 'devtools.timeline,v8,blink.user_timing,disabled-by-default-devtools.timeline,gpu', transferMode: 'ReturnAsStream' });
    let failure = null;
    const navigated = Date.now();
    try {
      await page.goto(runUrl.href, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForFunction(() => window.__initialLoad.ready !== null || window.__qaError, null, { timeout: 190000 });
      const qaError = await page.evaluate(() => window.__qaError);
      if (qaError) throw new Error(qaError);
      if (requirement === 'bundled') {
        assert.equal(await page.locator('.loading-active').textContent(), 'Ready · choose Multiplayer to enter the city');
        assert.ok(await page.locator('link[rel="modulepreload"]').count(), 'new Pages startup entry');
      }
      await page.screenshot({ path: path.join(out, `${cache}-ready.png`) });
      await page.evaluate(() => { window.__initialLoad.click = performance.now(); });
      await page.locator('[data-option="multiplayer"]').click({ timeout: 5000 });
      if (mobile) await page.evaluate(() => {
        const input = window.__game.input, update = input.update.bind(input);
        input.update = (...args) => { update(...args); input.lookX = 0.002; };
      });
      await page.waitForFunction(() => window.__initialLoad.done, null, { timeout: (seconds + 10) * 1000 });
      await page.screenshot({ path: path.join(out, `${cache}-playing.png`) });
      if (drive && cache === 'warm') {
        await page.evaluate(installScenario, { flightSeconds: 0 });
        await page.evaluate(() => window.__air.prepareDrive());
        await page.waitForFunction(() => window.__game.state().mode === 'car', null, { timeout: 45000 });
        assert.equal(await page.evaluate(() => window.__game.state().drivenId), 'player', 'route controls the occupied starter car');
        await page.evaluate(() => window.__air.beginDrive());
        await page.waitForTimeout(120000);
        await page.evaluate(() => window.__air.stop());
        fs.writeFileSync(path.join(out, 'drive.json'), JSON.stringify(await page.evaluate(() => ({
          frames: window.__air.frames, poses: window.__air.poses, route: window.__air.route,
          extensions: window.__air.extensions, perf: window.__perf, state: window.__game.state(),
        }))));
        const routeEvidence = await page.evaluate(() => {
          const points = window.__air.poses.filter(p => p.phase === 'drive').map(p => p.state.vehicle?.pos).filter(Boolean);
          return { distance: points.slice(1).reduce((n, p, i) => n + Math.hypot(p[0] - points[i][0], p[2] - points[i][2]), 0),
            tiles: [...new Set(points.map(p => `${Math.floor(p[0] / 250)},${Math.floor(p[2] / 250)}`))] };
        });
        const driving = await page.evaluate(() => window.__air.frames.filter(row => row[2] === 'drive').map(row => row[1]));
        driving.sort((a, b) => a - b);
        const summary = { cache: 'drive', ...routeEvidence, fps: driving.length * 1000 / driving.reduce((sum, ms) => sum + ms, 0), count: driving.length, p50: driving[Math.floor(driving.length * .5)],
          p95: driving[Math.floor(driving.length * .95)], p99: driving[Math.floor(driving.length * .99)],
          max: driving.at(-1), over50: driving.filter(ms => ms > 50).length };
        checks.push(summary); console.log(JSON.stringify(summary));
        await page.screenshot({ path: path.join(out, 'drive-end.png') });
      }
    } catch (e) { failure = String(e); }
    const result = await page.evaluate(() => ({ ...window.__initialLoad,
      paints: performance.getEntriesByType('paint').map(e => e.toJSON()),
      streaming: window.__streamingProfile, perf: window.__perf, timeOrigin: performance.timeOrigin,
      resources: performance.getEntriesByType('resource').map(e => e.toJSON()),
      load: window.__game?.loadProgress?.(), state: window.__game?.state?.(), error: window.__qaError,
    })).catch(() => ({}));
    result.metrics = (await cdp.send('Performance.getMetrics')).metrics;
    if (diagnostic) {
    fs.writeFileSync(path.join(out, `${cache}-cpu.json`), JSON.stringify(await cdp.send('Profiler.stop')));
    const trace = new Promise(resolve => cdp.once('Tracing.tracingComplete', resolve));
    await cdp.send('Tracing.end');
    const { stream } = await trace;
    const fd = fs.openSync(path.join(out, `${cache}-trace.json`), 'w');
    for (;;) { const chunk = await cdp.send('IO.read', { handle: stream }); fs.writeSync(fd, chunk.data); if (chunk.eof) break; }
    fs.closeSync(fd); await cdp.send('IO.close', { handle: stream });
    }
    const sorted = [...result.frames ?? []].sort((a, b) => a - b);
    const percentile = q => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))];
    const playBuilds = (result.streaming ?? []).filter(row => row.start >= result.firstFrame && row.kind.startsWith('node-build-'));
    result.summary = { label, cache, readyMs: result.ready, firstFrameMs: result.firstFrame,
      paintMs: result.paints?.find(e => e.name === 'first-contentful-paint')?.startTime,
      isolationReloadMs: result.timeOrigin - navigated,
      clickToFrameMs: result.firstFrame - result.click,
      nodeBuilds: { sync: playBuilds.filter(row => row.kind === 'node-build-sync').length, async: playBuilds.filter(row => row.kind === 'node-build-async').length },
      mobile, cpuThrottle: mobile ? 6 : 1,
      frames: { fps: sorted.length * 1000 / sorted.reduce((sum, ms) => sum + ms, 0), count: sorted.length, p50: percentile(.5), p95: percentile(.95), p99: percentile(.99), max: sorted.at(-1), over50: sorted.filter(n => n > 50).length },
      requests: requests.size, bytes: [...requests.values()].reduce((n, r) => n + (r.bytes ?? 0), 0), failure, errors };
    fs.writeFileSync(path.join(out, `${cache}.json`), JSON.stringify({ ...result, requests: [...requests.values()] }, null, 2));
    console.log(JSON.stringify(result.summary));
    checks.push({ cache, ...result.summary.frames });
    await page.close();
    await room.close(); room = null;
    if (failure) throw new Error(failure);
    if (process.env.TURKU3D_PROFILE_EDGE_WARMUP === '1') {
      assert(diagnostic, 'node-build validation needs streaming instrumentation');
      assert.equal(playBuilds.filter(row => row.kind === 'node-build-sync' && row.ancestry.includes('edge-marker')).length, 0, 'boundary bands reuse startup node builds');
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(result.state.errors, []);
    assert.ok(sorted.length > 100, 'real game frames');
    if (requirement === 'bundled') {
      assert.ok([...requests.values()].some(r => /bundle\.[0-9a-f]{64}\.col/.test(r.url)), 'deployed OCI collision bundles used');
      assert.equal(result.bounds.filter(b => b.at < result.ready).length, 1, 'loading panel never shifts before Ready');
    }
  }
  if (strict) for (const check of checks) {
    if (check.cache === 'drive') assert.ok(check.distance > 250 && check.tiles.length >= 3, 'drive crosses new tiles');
    assert.equal(check.over50, 0, `${check.cache}: zero frames over 50 ms`);
    assert.ok(check.p99 <= 33, `${check.cache}: p99 <= 33 ms (observed ${check.p99})`);
  }
} finally { clearTimeout(hardStop); await context.close(); await room?.close(); }
