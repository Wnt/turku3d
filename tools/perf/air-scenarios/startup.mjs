// Remote Air startup proof. Attach only to this task's exclusive air-session Chrome.
import { checkedRecorder } from './recorder.mjs';
import { chromium } from '../../../game/node_modules/playwright-core/index.mjs';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createServer } from '../../../server/http.js';
import { ScoreStore } from '../../../server/store.js';
import { reportsRoot, port } from '../../dev/environment.mjs';
const [label, cdpBase = `http://127.0.0.1:${port(8)}`, base, expectation = 'baseline'] = process.argv.slice(2);
if (!label || !base) throw new Error('startup.mjs LABEL CDP GAME_URL baseline|fixed');
const out = path.join(reportsRoot, 'start-loading', label);
fs.mkdirSync(out, { recursive: true });
const recorderFile = checkedRecorder(path.join(os.homedir(), 'turku3d/profiling/record.mjs'), out);
// A fresh sandbox room gives baseline and fixed runs the identical first assigned spawn.
const app = createServer({ store: new ScoreStore(path.join(out, 'scores')) });
await new Promise((resolve, reject) => {
  app.server.once('error', reject);
  app.server.listen(port(4), '127.0.0.1', resolve);
});
let browser;
const summaries = [];
try {
  browser = await chromium.connectOverCDP(cdpBase);
  for (const scenario of ['multiplayer', 'continue']) {
    const context = await browser.newContext({ viewport: null });
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
    await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
    await page.addInitScript(({ scenario }) => {
      localStorage.clear(); sessionStorage.clear();
      if (scenario === 'continue') localStorage.setItem('turku3d.saves', JSON.stringify({ version: 1, slots: {
        auto: { version: 1, savedAt: Date.now(), label: 'Remote Air startup', mission: '',
          player: { E: 23459918.3, N: 6704225.7, H: 9, yaw: 0 }, vehicle: null, clock: null,
          stats: { deliveries: 0, money: 120 } },
      } }));
      performance.setResourceTimingBufferSize(20000);
      const p = window.__startupAir = { click: null, enabled: null, frames: [], overlays: [], ages: [], lastState: null,
        lastFrame: null, heapBefore: null, heapAfter: null, recording: false, rtt: [], steps: [], sent: new Map() };
      const post = Worker.prototype.postMessage;
      Worker.prototype.postMessage = function (m, ...args) {
        if (m?.type === 'step') p.sent.set(m.seq, performance.now());
        return post.call(this, m, ...args);
      };
      const descriptor = Object.getOwnPropertyDescriptor(Worker.prototype, 'onmessage');
      Object.defineProperty(Worker.prototype, 'onmessage', { configurable: true,
        get() { return descriptor.get.call(this); },
        set(handler) { descriptor.set.call(this, handler && (event => {
          const m = event.data;
          if (m?.type === 'state') {
            p.lastState = performance.now();
            if (p.recording && p.sent.has(m.seq)) p.rtt.push(performance.now() - p.sent.get(m.seq));
            p.sent.delete(m.seq);
            if (p.recording) p.steps.push(m.stepMs);
          }
          return handler.call(this, event);
        })); },
      });
      document.addEventListener('pointerup', event => {
        if (!event.target.closest('[data-option="multiplayer"], [data-option="continue"]')) return;
        p.click = performance.now(); p.lastFrame = p.click; p.recording = true;
        p.heapBefore = performance.memory?.usedJSHeapSize ?? null;
      }, true);
      const sample = () => {
        const now = performance.now();
        if (p.recording) {
          p.frames.push(now - p.lastFrame); p.lastFrame = now;
          if (p.lastState !== null) p.ages.push(now - p.lastState);
          const style = getComputedStyle(document.getElementById('loading-screen'));
          const visible = style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) > 0;
          const s = window.__game?.state?.();
          p.overlays.push({ at: now, visible, loading: s?.loading, paused: s?.paused });
          if (p.enabled === null && s && !s.startScreen && !s.loading && !s.paused) p.enabled = performance.now();
          p.heapAfter = performance.memory?.usedJSHeapSize ?? null;
        }
        requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    }, { scenario });
    const requests = [], errors = [];
    cdp.on('Network.requestWillBeSent', e => requests.push({ at: e.wallTime * 1000, url: e.request.url, type: e.type }));
    page.on('pageerror', error => errors.push(error.message));
    const url = new URL(base);
    url.searchParams.set('mp', `ws://${url.host}/startup-mp`);
    url.searchParams.set('backend', 'webgpu');
    url.searchParams.set('capture', 'turku-game'); // Lets the existing recorder select this owned page over the blank launch tab.
    await page.goto(url.href);
    await page.waitForFunction(() => window.__game?.ready || window.__qaError, null, { timeout: 600000 });
    console.log(JSON.stringify({ label, scenario, stage: 'ready' }));
    const failure = await page.evaluate(() => window.__qaError);
    if (failure) throw new Error(failure);
    const ready = await page.evaluate(() => ({ progress: window.__game.loadProgress(), state: window.__game.state(),
      audio: window.__game.audio.snapshot(), voice: window.__game.voice.snapshot(),
      canvas: [document.querySelector('canvas').width, document.querySelector('canvas').height] }));
    await page.screenshot({ path: path.join(out, `${scenario}-ready.png`) });
    let badgeLayout = null;
    if (scenario === 'multiplayer' && url.pathname === '/') {
      badgeLayout = await page.evaluate(async () => {
        const { BuildBadge } = await import('/src/ui/buildBadge.ts');
        const { CoarseTiles } = await import('/src/world/coarseTiles.ts');
        const build = new BuildBadge(document.body);
        build.set({ service: true, commit: '1234567', tiles: { building: ['t0_0_0', 't0_1_0', 't0_1_1', 't0_2_1'] } });
        const draft = new CoarseTiles({ manifest: { visual: {} }, loadModel: async () => null,
          tiles: { addEventListener() {} }, badgeParent: document.body });
        draft.show('drafts t0_0_0 t0_0_1 t0_1_0 t0_1_1 t0_2_0 t0_2_1 t0_3_1 t0_3_2 t0_4_1 t0_4_2 t0_5_1 t0_5_2 · 12 building');
        const a = build.element.getBoundingClientRect(), b = draft.badge.getBoundingClientRect();
        window.__startupBadges = [build.element, draft.badge];
        return { build: a.toJSON(), draft: b.toJSON(), overlap: Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left))
          * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)) };
      });
      await page.screenshot({ path: path.join(out, 'badge-layout.png') });
      await page.evaluate(() => { window.__startupBadges.forEach(e => e.remove()); delete window.__startupBadges; });
    }
    const recorderDir = path.join(out, `${scenario}-record`);
    fs.rmSync(recorderDir, { recursive: true, force: true });
    fs.mkdirSync(recorderDir, { recursive: true });
    const recorder = spawn(process.execPath, [recorderFile,
      cdpBase, 'current', '30', recorderDir], { stdio: ['ignore', 'pipe', 'pipe'] });
    let recorderLog = '';
    recorder.stdout.on('data', chunk => { recorderLog += chunk; });
    recorder.stderr.on('data', chunk => { recorderLog += chunk; });
    const recorderDone = new Promise(resolve => recorder.on('exit', code => resolve(code)));
    for (let i = 0; i < 100 && !recorderLog.includes('tracing started'); i++) await new Promise(r => setTimeout(r, 100));
    await page.locator(`[data-option="${scenario}"]`).click();
    await page.waitForFunction(() => window.__startupAir.enabled !== null, null, { timeout: 120000, polling: 'raf' });
    const transition = await page.evaluate(() => ({ enabled: window.__startupAir.enabled, state: window.__game.state() }));
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'w', code: 'KeyW', windowsVirtualKeyCode: 87 });
    await page.waitForTimeout(1000);
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'w', code: 'KeyW', windowsVirtualKeyCode: 87 });
    await page.waitForTimeout(5000);
    const result = await page.evaluate(() => {
      window.__startupAir.recording = false;
      const probe = { ...window.__startupAir };
      delete probe.sent;
      return { ...probe, origin: performance.timeOrigin, state: window.__game.state() };
    });
    fs.writeFileSync(path.join(recorderDir, 'STOP'), 'done\n');
    const recorderCode = await recorderDone;
    fs.writeFileSync(path.join(recorderDir, 'stdout.log'), recorderLog);
    const recorded = JSON.parse(fs.readFileSync(path.join(recorderDir, 'frames.json'), 'utf8'));
    if (!recorded.length) throw new Error(`External recorder produced no samples: ${recorderDir}`);
    await page.screenshot({ path: path.join(out, `${scenario}-playing.png`) });
    const afterClick = requests.filter(r => r.at >= result.origin + result.click);
    const duringTransition = afterClick.filter(r => r.at <= result.origin + result.enabled);
    const worldRequests = duringTransition.filter(r => /\.(?:glb|col|ktx2)(?:\?|$)|\/ground\//.test(r.url));
    const stats = values => {
      const sorted = [...values].sort((a, b) => a - b);
      return { p50: sorted[Math.floor(sorted.length * 0.5)] ?? null, p95: sorted[Math.floor(sorted.length * 0.95)] ?? null,
        max: sorted.at(-1) ?? null };
    };
    const summary = { label, scenario, badgeLayout, latencyMs: result.enabled - result.click, frameMs: stats(result.frames),
      stateAgeMs: stats(result.ages), physicsRttMs: stats(result.rtt), workerStepMs: stats(result.steps),
      heapGrowthBytes: result.heapAfter - result.heapBefore, transitionWorldRequests: worldRequests.length,
      transitionRequests: duringTransition.length, postClickRequests: afterClick.length,
      overlayFrames: result.overlays.filter(s => s.visible).length, canvas: ready.canvas, recorderCode,
      feetAtEnabled: transition.state.feet, feetAfter: result.state.feet,
      errors: [...new Set([...errors, ...result.state.errors])] };
    const report = { summary, ready, transition, result, afterClick, duringTransition, worldRequests, requests };
    fs.writeFileSync(path.join(out, `${scenario}.json`), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(summary));
    summaries.push(summary);
    await context.close();
  }
} finally { await browser?.close(); await app.close(); }
fs.writeFileSync(path.join(out, 'summary.json'), JSON.stringify(summaries, null, 2));
if (expectation === 'fixed' && summaries.some(s => s.latencyMs >= 200 || s.transitionWorldRequests || s.overlayFrames || s.errors.length || s.badgeLayout?.overlap)) {
  throw new Error(`Air startup acceptance failed; see ${out}`);
}
