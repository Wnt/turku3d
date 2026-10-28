import { chromeArgs } from '../../tools/dev/chrome-args.mjs';
// Manual cold-cache/first-turn measurement; deliberately outside the gate.
import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { execFileSync } from 'node:child_process';
import { isolateBrowser, reportsRoot, port } from '../../tools/dev/environment.mjs';

isolateBrowser();
const [label = 'after', backend = 'webgpu', base = `http://127.0.0.1:${port(1)}/`] = process.argv.slice(2);
const out = path.join(reportsRoot, 'preload', `${label}-${backend}`);
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true,
  args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
try {
  const browserCdp = await browser.newBrowserCDPSession();
  const memory = async () => {
    const { processInfo } = await browserCdp.send('SystemInfo.getProcessInfo');
    const pids = processInfo.map(p => p.id).filter(Number.isInteger);
    const rssKB = execFileSync('ps', ['-o', 'rss=', '-p', pids.join(',')], { encoding: 'utf8' })
      .trim().split(/\s+/).map(Number).reduce((sum, n) => sum + n, 0);
    return { browserRssBytes: rssKB * 1024, processes: pids.length };
  };
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
  await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 40,
    downloadThroughput: 10_000_000 / 8, uploadThroughput: 1_000_000 / 8 });
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'getGamepads', { value: () => [] });
    performance.setResourceTimingBufferSize(10000);
  });
  const requests = [], errors = [];
  page.on('console', m => { if (m.type() === 'error') { errors.push(m.text()); console.log(m.text()); } });
  page.on('request', r => requests.push({ at: Date.now(), url: r.url() }));
  page.on('pageerror', e => errors.push(e.message));
  const started = Date.now();
  const url = new URL(base);
  url.searchParams.set('backend', backend);
  url.searchParams.set('test', '');
  url.searchParams.set('time', '2026-06-21T10:00:00Z');
  await page.goto(url.href);
  await page.waitForFunction(() => window.__game?.ready || window.__qaError, null, { timeout: 600000 });
  if (await page.evaluate(() => window.__qaError)) throw new Error(await page.evaluate(() => window.__qaError));
  const readyMs = Date.now() - started;
  const preloadMemory = await memory();
  // The old start path releases controls before streaming. Record both that instant and its idle view.
  await page.screenshot({ path: path.join(out, 'ready.png') });
  if (label === 'before') await page.waitForTimeout(20000);
  if (label === 'before-settled') await page.waitForFunction(() => window.__game.state().tilesSettled, null, { timeout: 300000 });
  const snapshot = () => page.evaluate(() => ({ state: window.__game.state(),
    progress: window.__game.loadProgress?.(), memory: performance.memory && {
      usedJSHeapSize: performance.memory.usedJSHeapSize, totalJSHeapSize: performance.memory.totalJSHeapSize },
    renderer: window.__render.renderer.info.memory,
    tileCacheBytes: window.__game.tiles.tiles.lruCache.cachedBytes,
    loaded: [...window.__game.tiles.tiles.activeTiles].map(t => t.extras?.id) }));
  const initial = await snapshot();
  const initialMemory = await memory();
  // Select New game through its normal menu. Public releases already have player control.
  if (await page.locator('[data-id="new"]').count()) await page.locator('[data-id="new"]').click();
  else if (await page.evaluate(() => window.__game.state().startScreen)) await page.keyboard.press('Enter');
  await page.waitForFunction(() => !window.__game.state().startScreen && !window.__game.state().loading, null, { timeout: 120000 });
  await page.screenshot({ path: path.join(out, 'initial.png') });
  const turnStart = Date.now();
  const frames = await page.evaluate(() => new Promise(resolve => {
    const intervals = [];
    let last = performance.now(), n = 0;
    const yaw = window.__game.orbit.yaw;
    const step = now => {
      intervals.push(now - last); last = now;
      window.__game.orbit.yaw = yaw + 2 * Math.PI * ++n / 240;
      if (n >= 240) resolve(intervals); else requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }));
  const turnEnd = Date.now();
  await page.waitForTimeout(3000);
  const after = await snapshot();
  const turnRequests = requests.filter(r => r.at >= turnStart && /\.(?:glb|ktx2|col)(?:\?|$)|\/ground\//.test(r.url));
  const sorted = [...frames].sort((a, b) => a - b);
  const report = { label, backend, hostLoad: os.loadavg(), networkMbps: 10, latencyMs: 40, cpuThrottle: 4, preloadMemory, initialMemory,
    readyMs, initial, after, errors, turnRequests, turnMs: turnEnd - turnStart,
    frameMs: { p50: sorted[Math.floor(sorted.length * .5)], p95: sorted[Math.floor(sorted.length * .95)],
      max: sorted.at(-1), mean: frames.reduce((a, b) => a + b, 0) / frames.length }, requests };
  fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  await page.screenshot({ path: path.join(out, 'turned.png') });
  console.log(JSON.stringify({ ...report, initial: { loaded: initial.loaded, memory: initial.memory,
    renderer: initial.renderer, tileCacheBytes: initial.tileCacheBytes }, after: { loaded: after.loaded },
    turnRequests: turnRequests.length, requests: requests.length }, null, 2));
  if (!label.startsWith('before') && (turnRequests.length || errors.length)) throw new Error('first-turn asset requests or page errors; see report.json');
} finally { await browser.close(); }
