// Deployed cold/warm startup evidence. Run under host_slots.py browser; no local server or synthetic tiles.
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const [label = 'current', url = 'https://wnt.github.io/turku-game/game/'] = process.argv.slice(2);
const out = path.join(reportsRoot, 'initial-load', label);
fs.mkdirSync(out, { recursive: true });
const context = await chromium.launchPersistentContext(path.join(process.env.TURKU3D_BROWSER_DIR, `initial-load-${label}`), {
  executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs, headless: true,
  viewport: { width: 2560, height: 1440 }, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']),
});
const hardStop = setTimeout(() => { void context.close(); }, 440_000);
try {
  for (const cache of ['cold', 'warm']) {
    const page = await context.newPage(), cdp = await context.newCDPSession(page);
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
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => {
      localStorage.clear(); sessionStorage.clear();
      performance.setResourceTimingBufferSize(10000);
      const p = window.__initialLoad = { ready: null, click: null, firstFrame: null, frames: [], tasks: [], shifts: [], log: [], stages: [], bounds: [] };
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
          if (prev) p.frames.push(now - prev);
          prev = now;
        }
        if (p.firstFrame === null || now - p.firstFrame < 10_000) requestAnimationFrame(frame);
        else p.done = true;
      };
      requestAnimationFrame(frame);
    });
    await cdp.send('Tracing.start', { categories: 'devtools.timeline,v8,blink.user_timing,disabled-by-default-devtools.timeline,gpu', transferMode: 'ReturnAsStream' });
    let failure = null;
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForFunction(() => window.__initialLoad.ready !== null || window.__qaError, null, { timeout: 190000 });
      const qaError = await page.evaluate(() => window.__qaError);
      if (qaError) throw new Error(qaError);
      await page.screenshot({ path: path.join(out, `${cache}-ready.png`) });
      await page.evaluate(() => { window.__initialLoad.click = performance.now(); });
      await page.locator('[data-option="multiplayer"]').click({ timeout: 5000 });
      await page.waitForFunction(() => window.__initialLoad.done, null, { timeout: 20000 });
      await page.screenshot({ path: path.join(out, `${cache}-playing.png`) });
    } catch (e) { failure = String(e); }
    const result = await page.evaluate(() => ({ ...window.__initialLoad,
      paints: performance.getEntriesByType('paint').map(e => e.toJSON()),
      resources: performance.getEntriesByType('resource').map(e => e.toJSON()),
      load: window.__game?.loadProgress?.(), state: window.__game?.state?.(), error: window.__qaError,
    })).catch(() => ({}));
    result.metrics = (await cdp.send('Performance.getMetrics')).metrics;
    const trace = new Promise(resolve => cdp.once('Tracing.tracingComplete', resolve));
    await cdp.send('Tracing.end');
    const { stream } = await trace;
    const fd = fs.openSync(path.join(out, `${cache}-trace.json`), 'w');
    for (;;) { const chunk = await cdp.send('IO.read', { handle: stream }); fs.writeSync(fd, chunk.data); if (chunk.eof) break; }
    fs.closeSync(fd); await cdp.send('IO.close', { handle: stream });
    const sorted = [...result.frames ?? []].sort((a, b) => a - b);
    const percentile = q => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))];
    result.summary = { label, cache, readyMs: result.ready, firstFrameMs: result.firstFrame,
      paintMs: result.paints?.find(e => e.name === 'first-contentful-paint')?.startTime,
      frames: { count: sorted.length, p50: percentile(.5), p95: percentile(.95), p99: percentile(.99), max: sorted.at(-1), over50: sorted.filter(n => n > 50).length },
      requests: requests.size, bytes: [...requests.values()].reduce((n, r) => n + (r.bytes ?? 0), 0), failure, errors };
    fs.writeFileSync(path.join(out, `${cache}.json`), JSON.stringify({ ...result, requests: [...requests.values()] }, null, 2));
    console.log(JSON.stringify(result.summary));
    await page.close();
    if (failure) throw new Error(failure);
  }
} finally { clearTimeout(hardStop); await context.close(); }
