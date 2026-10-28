// Manual real-data startup proof, outside the gate. Run through host_slots.py browser.
import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { isolateBrowser, reportsRoot, port } from '../../tools/dev/environment.mjs';
import WebSocket from '../../server/node_modules/ws/wrapper.mjs';

isolateBrowser();
const [label = 'localhost', base = `http://localhost:${port()}/`] = process.argv.slice(2);
const out = path.join(reportsRoot, 'start-loading', label);
fs.mkdirSync(out, { recursive: true });
const mp = `ws://127.0.0.1:${port(4)}/`;
const retained = { E: 410, N: -280, H: 9, yaw: 0 };
async function seedSession() {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(mp, { origin: `http://localhost:${port()}` });
    ws.on('error', reject);
    ws.on('open', () => ws.send(JSON.stringify({ v: 1, type: 'hello' })));
    ws.on('message', raw => {
      const m = JSON.parse(String(raw));
      if (m.type !== 'welcome') return;
      ws.send(JSON.stringify({ v: 1, type: 'pose', ...retained, t: 1, vE: 0, vN: 0, vH: 0,
        mode: 'foot', vehicle: null, carrying: false }));
      setTimeout(() => { ws.close(); resolve(m.session); }, 100);
    });
  });
}
const browser = await chromium.launch({ channel: 'chrome', headless: true,
  args: ['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist'] });
try {
  for (const scenario of ['multiplayer', 'continue', 'retained-multiplayer']) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
    await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
    const token = scenario === 'retained-multiplayer' ? await seedSession() : null;
    await page.addInitScript(({ token, retained, scenario }) => {
      localStorage.clear(); sessionStorage.clear();
      if (token) sessionStorage.setItem('turku3d.mp.session.v1', token);
      if (scenario === 'continue') localStorage.setItem('turku3d.saves', JSON.stringify({ version: 1, slots: {
        auto: { version: 1, savedAt: Date.now(), label: 'Remote startup probe', mission: '',
          player: { E: 23459500 + retained.E, N: 6704500 + retained.N, H: retained.H, yaw: retained.yaw },
          vehicle: null, clock: null, stats: { deliveries: 0, money: 120 } },
      } }));
      performance.setResourceTimingBufferSize(10000);
      window.__startupProbe = { samples: [], entered: null, enabled: null };
      document.addEventListener('pointerup', event => {
        if (!event.target.closest('[data-option="multiplayer"], [data-option="continue"]')) return;
        const probe = window.__startupProbe;
        probe.entered = performance.now();
        const sample = () => {
          const state = window.__game.state();
          const style = getComputedStyle(document.getElementById('loading-screen'));
          probe.samples.push({ at: performance.now(), overlay: style.display !== 'none' && style.visibility !== 'hidden',
            loading: state.loading, paused: state.paused, startScreen: state.startScreen });
          if (!state.startScreen && !state.loading && !state.paused) probe.enabled = performance.now();
          else requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
      }, true);
    }, { token, retained, scenario });
    const requests = [], errors = [];
    page.on('request', r => requests.push({ at: Date.now(), url: r.url() }));
    page.on('pageerror', e => errors.push(e.message));
    const url = new URL(base);
    // The multiplayer server admits localhost origins, matching the actual local game.
    if (url.hostname === '127.0.0.1') url.hostname = 'localhost';
    url.searchParams.set('mp', mp); url.searchParams.set('backend', 'webgpu');
    await page.goto(url.href);
    await page.waitForFunction(() => window.__game?.ready || window.__qaError, null, { timeout: 600000 });
    const failure = await page.evaluate(() => window.__qaError);
    if (failure) throw new Error(failure);
    const ready = await page.evaluate(() => ({ state: window.__game.state(), progress: window.__game.loadProgress(),
      audio: window.__game.audio.snapshot(), voice: window.__game.voice.snapshot() }));
    await page.screenshot({ path: path.join(out, `${scenario}-ready.png`) });
    await page.locator(`[data-option="${scenario === 'continue' ? 'continue' : 'multiplayer'}"]`).click();
    await page.waitForFunction(() => window.__startupProbe.enabled !== null, null, { timeout: 30000, polling: 'raf' });
    const result = await page.evaluate(() => ({ ...window.__startupProbe, origin: performance.timeOrigin, state: window.__game.state() }));
    const transitionRequests = requests.filter(r => r.at >= result.origin + result.entered && r.at <= result.origin + result.enabled);
    const assetRequests = transitionRequests.filter(r => /\.(?:glb|col|ktx2|ogg)(?:\?|$)|\/ground\//.test(r.url));
    await page.keyboard.down('w');
    await page.waitForTimeout(500);
    await page.keyboard.up('w');
    const moved = await page.evaluate(() => window.__game.state());
    await page.screenshot({ path: path.join(out, `${scenario}-playing.png`) });
    errors.push(...result.state.errors);
    const report = { scenario, label, url: url.href, ready, ...result, movementAfter500ms: moved.feet,
      latencyMs: result.enabled - result.entered, transitionRequests, assetRequests, requests, errors };
    fs.writeFileSync(path.join(out, `${scenario}.json`), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ scenario, latencyMs: report.latencyMs, assetRequests, errors,
      feet: result.state.feet, movementAfter500ms: moved.feet, overlaySamples: result.samples }));
    if (report.latencyMs >= 200 || assetRequests.length || errors.length || result.samples.some(s => s.overlay)) {
      throw new Error(`${scenario}: startup acceptance failed; see ${out}`);
    }
    await context.close();
  }
} finally { await browser.close(); }
