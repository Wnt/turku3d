// Manual real-data measurement, outside the gate; run with host_slots.py browser.
import { chromium } from '@playwright/test';
import { chromeArgs, chromeExecutable, chromeIgnoreDefaultArgs } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot, port } from '../../tools/dev/environment.mjs';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
isolateBrowser();
const [label = 'before', base = `http://127.0.0.1:${port()}/`, action = 'startup', gameRoot] = process.argv.slice(2);
const out = path.join(reportsRoot, 'fly-fallthrough', label);
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ executablePath: chromeExecutable(),
  ignoreDefaultArgs: chromeIgnoreDefaultArgs, headless: true,
  args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, serviceWorkers: gameRoot ? 'block' : 'allow' });
  // Compare a local production bundle against the actual Pages origin and its unchanged hosted tiles.
  if (gameRoot) await page.route('https://wnt.github.io/turku-game/game/**', async route => {
    const relative = decodeURIComponent(new URL(route.request().url()).pathname.slice('/turku-game/game/'.length)) || 'index.html';
    const file = path.resolve(gameRoot, relative);
    if (!file.startsWith(path.resolve(gameRoot) + path.sep)) return route.abort();
    await route.fulfill({ path: file, headers: { 'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'require-corp' } });
  });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
  await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await page.addInitScript(() => {
    localStorage.clear(); sessionStorage.clear(); performance.setResourceTimingBufferSize(30000);
    window.__loadSamples = [];
    window.__entryProbe = { clicked: null, enabled: null, frames: [] };
    document.addEventListener('pointerup', event => {
      if (!event.target.closest('[data-option="new"], [data-option="multiplayer"], [data-option="continue"]')) return;
      const p = window.__entryProbe;
      p.clicked = performance.now();
      const sample = () => {
        const s = window.__game.state(), overlay = !document.getElementById('loading-screen').hidden;
        p.frames.push({ at: performance.now(), overlay, loading: s.loading, paused: s.paused });
        if (!s.startScreen && !s.loading && !s.paused) p.enabled = performance.now();
        else requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    }, true);
    const seen = new Set();
    setInterval(() => {
      const log = document.querySelector('.loading-log')?.textContent ?? '';
      const active = document.querySelector('.loading-active')?.textContent ?? '';
      for (const line of log.split('\n')) if (line && !seen.has(line)) {
        seen.add(line); window.__loadSamples.push({ at: performance.now(), line });
      }
      window.__loadActive = active;
    }, 50);
  });
  const errors = [];
  page.on('pageerror', e => { errors.push(e.message); console.log(e.message); });
  page.on('console', m => { if (m.type() === 'error') console.log(m.text()); });
  const url = new URL(base); url.searchParams.set('backend', 'webgpu');
  await page.goto(url.href);
  let ready = false;
  for (let i = 0; i < 360; i++) {
    const state = await page.evaluate(() => ({ ready: !!window.__game?.ready, error: window.__qaError,
      progress: window.__game?.loadProgress?.(), active: window.__loadActive })).catch(() => ({ ready: false, active: 'navigation' }));
    if (i % 15 === 0) console.log(JSON.stringify(state));
    if (state.error) throw new Error(state.error);
    if (state.ready) { ready = true; break; }
    await page.waitForTimeout(1000);
  }
  const startup = await page.evaluate(() => ({ at: performance.now(), progress: window.__game?.loadProgress?.(),
    state: window.__game?.state?.(), samples: window.__loadSamples, active: window.__loadActive,
    resources: performance.getEntriesByType('resource').map(e => ({ url: e.name, start: e.startTime, ms: e.duration, bytes: e.encodedBodySize })) }));
  fs.writeFileSync(path.join(out, 'startup.json'), JSON.stringify({ ready, ...startup, errors, hostLoad: os.loadavg(), source: gameRoot ?? base }, null, 2));
  await page.screenshot({ path: path.join(out, 'ready.png') });
  console.log(JSON.stringify({ ready, at: startup.at, progress: startup.progress }));
  if (!ready) throw new Error('Ready timed out');
  if (action === 'transition') {
    const option = await page.locator('[data-option="multiplayer"]:not([aria-disabled="true"])').count() ? 'multiplayer' : 'new';
    await page.locator(`[data-option="${option}"]`).click();
    await page.waitForFunction(() => window.__entryProbe.enabled !== null, null, { timeout: 30000 });
    const result = await page.evaluate(() => ({ ...window.__entryProbe, state: window.__game.state() }));
    await page.waitForTimeout(300);
    const resources = await page.evaluate(() => performance.getEntriesByType('resource').map(e => ({ url: e.name, start: e.startTime })));
    const requests = resources.filter(e => e.start >= result.clicked && e.start <= result.enabled && /\.(?:glb|col|ktx2|ogg)(?:\?|$)|\/ground\//.test(e.url));
    const transition = { ...result, latencyMs: result.enabled - result.clicked, requests };
    fs.writeFileSync(path.join(out, 'transition.json'), JSON.stringify(transition, null, 2));
    console.log(JSON.stringify({ latencyMs: transition.latencyMs, requests, frames: result.frames }));
    if (!label.startsWith('before') && (transition.latencyMs >= 200 || requests.length || result.frames.some(f => f.overlay))) throw new Error('Entry acceptance failed');
  }
  if (action === 'fly' || action === 'drive') {
    await page.locator('[data-option="new"]').click();
    await page.waitForFunction(() => !window.__game.state().startScreen && !window.__game.state().loading);
    const samples = [];
    const sample = async name => {
      const s = await page.evaluate(() => ({ state: window.__game.state(), loaded: [...window.__game.physics.loaded.keys()] }));
      samples.push({ name, ...s }); console.log(JSON.stringify({ name, feet: s.state.feet, fly: s.state.fly, car: s.state.vehicle?.pos }));
    };
    await sample('start');
    if (action === 'fly') {
    await page.keyboard.press('Space'); await page.waitForTimeout(100); await page.keyboard.press('Space');
    await page.keyboard.down('Space'); await page.keyboard.down('Shift');
    await page.waitForTimeout(9000); await page.keyboard.up('Space');
    await page.keyboard.down('w');
    for (let i = 0; i < 12; i++) { await page.waitForTimeout(10000); await sample(`flight-${i}`); }
    await page.keyboard.up('w'); await page.keyboard.up('Shift');
    await sample('before-landing');
    await page.keyboard.press('Space'); await page.waitForTimeout(100); await page.keyboard.press('Space');
    for (let i = 0; i < 10; i++) { await page.waitForTimeout(3000); await sample(`landing-${i}`); }
    await page.screenshot({ path: path.join(out, 'landed.png') });
    fs.writeFileSync(path.join(out, 'flight.json'), JSON.stringify(samples, null, 2));
    }
    // Use the real pause-menu respawn to return to the evicted car, then walk and drive.
    await page.keyboard.press('Escape');
    await page.locator('[data-option="respawn"]').click();
    await page.locator('[data-option="yes"]').click();
    await page.waitForFunction(() => !window.__game.state().loading && !window.__game.state().paused, null, { timeout: 120000 });
    await sample('returned');
    await page.keyboard.down('w'); await page.waitForTimeout(1200); await page.keyboard.up('w');
    await sample('walked');
    // Place the capsule beside the car through the worker's normal placement API; F still owns entry.
    await page.evaluate(() => {
      const g = window.__game, car = g.state().vehicles.find(v => v.id === 'player');
      g.physics.setCharacterEnabled(true, { x: car.pos[0], y: car.pos[1] - 0.5, z: car.pos[2] + 2 });
    });
    await page.waitForTimeout(500); await page.keyboard.press('f');
    await page.waitForFunction(() => window.__game.state().mode === 'car');
    await sample('entered-car');
    await page.keyboard.down('w'); await page.waitForTimeout(4000); await page.keyboard.up('w');
    await sample('drove');
    await page.screenshot({ path: path.join(out, 'drove.png') });
    fs.writeFileSync(path.join(out, 'flight.json'), JSON.stringify(samples, null, 2));
    if (!label.startsWith('before') && samples.some(s => s.state.feet[1] < -20 || s.state.vehicle?.pos[1] < -20)) {
      throw new Error('Actor fell below the real route; see flight.json');
    }
  }
} finally { await browser.close(); }
