// Helpers for the in-game sound checks: drive the real game in the leased, muted Chrome over CDP and read the audio
// graph's state from the page (never the speakers). Used by check-*.mjs:
//   python3 ~/turku3d/tools/host-lease/session.py run mac sounds-fable --kind func --slots 1 --headless -- \
//     env BASE=http://localhost:5390/ node tools/sounds-fable/game/check-tyres.mjs OUT.json
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const REPO = path.join(os.homedir(), 'turku3d/wt/loop-integration/repo');
const { chromium } = await import(pathToFileURL(path.join(REPO, 'game/node_modules/@playwright/test/index.mjs')).href);
export const BASE = process.env.BASE ?? 'http://localhost:5390/';
export const sleep = ms => new Promise(r => setTimeout(r, ms));
export const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

export async function connect() { return chromium.connectOverCDP(process.env.BROWSER_CDP); }

/** Opens the game in a new window, starts `option` ('new' | 'multiplayer') and waits until it is playable. */
export async function openGame(browser, { option = 'new', query = '', label = 'game' } = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => { errors.push(e.message.slice(0, 300)); log(`${label} pageerror: ${e.message.slice(0, 200)}`); });
  // A local dev server has no multiplayer server (its refused WebSocket is not a finding). Failed requests are
  // reported with their URL from the response listener; the OCI copy has no /coarse/demand endpoint (404, as before).
  page.on('console', m => { if (m.type() === 'error' && !/WebSocket connection|Failed to load resource/.test(m.text())) errors.push('console: ' + m.text().slice(0, 300)); });
  // (The OCI copy also answers 422 for tiles outside its exported city: the harbour is only on city.lab.)
  page.on('response', r => { if (r.status() >= 400 && !/\/coarse\/(demand|want)/.test(r.url()) && !(r.status() === 422 && /\/coarse\//.test(r.url()))) errors.push(`HTTP ${r.status()} ${r.url().slice(0, 140)}`); });
  // Audio downloads with their time since navigation: which sound files load before the menu, before playable, after.
  const t0 = Date.now(), audioRequests = [], marks = {};
  page.on('request', r => { if (/\/assets\/(audio|voice)\/|\.(ogg|opus|mp3)(\?|$)/.test(r.url())) audioRequests.push({ file: r.url().split('/assets/').pop().slice(0, 80), s: +((Date.now() - t0) / 1000).toFixed(2) }); });
  await page.goto(`${BASE}?quality=low&telemetry=0${query}`, { waitUntil: 'domcontentloaded', timeout: 180000 });
  await page.waitForFunction(() => window.__game?.ready, null, { timeout: 400000 });
  const atReady = await page.evaluate(() => window.__game.audio.snapshot());
  marks.ready = +((Date.now() - t0) / 1000).toFixed(2);
  for (let k = 0; k < 10 && await page.evaluate(() => window.__game.state().startScreen); k++) {
    await page.evaluate(() => { for (const b of document.querySelectorAll('.browser-warning button, [class*="warning"] button')) if (b.offsetParent) b.click(); });
    await page.locator(`[data-option="${option}"]`).first().click({ timeout: 5000 }).catch(() => {});
    if (await page.evaluate(() => window.__game.state().startScreen)) await page.evaluate(o => document.querySelector(`[data-option="${o}"]`)?.dispatchEvent(new PointerEvent('pointerup', { bubbles: true })), option);
    await sleep(1500);
  }
  await page.waitForFunction(() => { const s = window.__game.state(); return !s.loading && !s.startScreen; }, null, { timeout: 400000 });
  marks.playable = +((Date.now() - t0) / 1000).toFixed(2);
  // The story intro (a new game): skip it.
  for (let k = 0; k < 6; k++) {
    const skipped = await page.evaluate(() => { const b = [...document.querySelectorAll('button')].find(b => b.offsetParent && /skip/i.test(b.textContent ?? '')); if (b) b.click(); return !!b; });
    if (!skipped) break;
    await sleep(500);
  }
  // A real key press unlocks audio for certain (the menu click may have been a scripted pointerup).
  await page.keyboard.press('KeyH'); await page.keyboard.press('KeyH');
  await page.waitForFunction(() => window.__game.audio.snapshot().state === 'running', null, { timeout: 20000 }).catch(() => log(`${label}: audio context did not start`));
  await page.evaluate(() => {
    const f = window.__fps = { n: 0, fps: 0, t: performance.now(), frames: [] };
    const tick = () => { f.n++; const now = performance.now(); if (now - f.t >= 1000) { f.fps = f.n * 1000 / (now - f.t); f.frames.push(+f.fps.toFixed(1)); f.n = 0; f.t = now; } requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  });
  log(`${label}: playable`);
  return { context, page, errors, atReady, audioRequests, marks, seconds: () => +((Date.now() - t0) / 1000).toFixed(2) };
}

/** Takes the nearest car (not a scooter or truck unless `kind` asks) and optionally puts it on the road at x, z. */
export async function takeCar(page, { place = null, kind = 'car' } = {}) {
  const car = await page.evaluate(async kind => {
    const g = window.__game, [x, , z] = g.state().feet;
    for (let t = 0; t < 40; t++) {
      const cars = (g.physics.latest?.vehicles ?? []).filter(v => kind === 'car' ? !/scooter|truck/.test(v.id) : new RegExp(kind).test(v.id) || v.kind === kind)
        .map(v => ({ id: v.id, kind: v.kind, d: Math.hypot(v.pos.x - x, v.pos.z - z) })).filter(c => c.d < 300).sort((a, b) => a.d - b.d);
      if (cars.length) { g.enterVehicle(cars[0].id); return cars[0]; }
      await new Promise(r => setTimeout(r, 500));
    }
    return null;
  }, kind);
  if (!car) throw new Error(`no ${kind} within 300 m`);
  await page.waitForFunction(() => window.__game.state().mode === 'car', null, { timeout: 15000 });
  if (place) { await page.evaluate(p => window.__game.relocateVehicle(p.x, p.z, p.yaw ?? 0), place); await sleep(4000); }
  return car;
}

/** Starts recording every `car` audio event of the page (the local vehicle's tyre frame per frame). */
export function recordCarEvents(page) {
  return page.evaluate(() => {
    const audio = window.__game.audio, rec = window.__carRec = [];
    if (audio.__recording) return;
    audio.__recording = true;
    const emit = audio.emit.bind(audio);
    audio.emit = event => { if (event.type === 'car') rec.push([performance.now(), event.tyres?.slide ?? -1, event.tyres?.load ?? 0, event.speed, event.tyres?.squeal ?? 0, event.tyres?.grit ?? 0]); return emit(event); };
  });
}
/** Drains the recording: per-frame [ms, slide, load, speed, squeal, grit]. */
export const drainCarEvents = page => page.evaluate(() => window.__carRec.splice(0));

export function summarise(rows) {
  const slides = rows.map(r => r[1]), sliding = slides.filter(v => v > 0.02);
  const seconds = rows.length ? (rows.at(-1)[0] - rows[0][0]) / 1000 : 0;
  // Bursts: runs of sliding frames.
  let bursts = 0, inBurst = false;
  for (const v of slides) { if (v > 0.02 && !inBurst) bursts++; inBurst = v > 0.02; }
  const loads = rows.filter(r => r[1] > 0.02).map(r => r[2]);
  return { frames: rows.length, seconds: +seconds.toFixed(1), slidingShare: +(sliding.length / Math.max(1, rows.length)).toFixed(4), bursts,
    maxSlide: +Math.max(0, ...slides).toFixed(2), meanSlide: +(sliding.reduce((a, b) => a + b, 0) / Math.max(1, sliding.length)).toFixed(2),
    topSpeed: +Math.max(0, ...rows.map(r => Math.abs(r[3]))).toFixed(1), loadRange: loads.length ? [+Math.min(...loads).toFixed(2), +Math.max(...loads).toFixed(2)] : null };
}

export const fps = (page, seconds) => page.evaluate(async s => { const f = window.__fps; f.frames.length = 0; await new Promise(r => setTimeout(r, s * 1000 + 200));
  const sorted = [...f.frames].sort((a, b) => a - b); return { mean: +(f.frames.reduce((a, b) => a + b, 0) / f.frames.length).toFixed(1), min: sorted[0], median: sorted[sorted.length >> 1], samples: f.frames.length }; }, seconds);

/** Frame rate with and without a load, interleaved: `rounds` x (`seconds` off, `seconds` on). Returns both means. */
export async function fpsAB(page, on, off, { rounds = 4, seconds = 8 } = {}) {
  const a = [], b = [];
  for (let i = 0; i < rounds; i++) {
    await off(); await sleep(600); a.push((await fps(page, seconds)).mean);
    await on(); await sleep(600); b.push((await fps(page, seconds)).mean);
  }
  await off();
  const mean = v => +(v.reduce((x, y) => x + y, 0) / v.length).toFixed(2);
  return { off: a, on: b, offMean: mean(a), onMean: mean(b), change: +((mean(b) / mean(a) - 1) * 100).toFixed(2) };
}

/** The audio thread's load (Chrome's AudioContext.renderCapacity, where it exists): mean and peak share of each render quantum's budget. */
export const audioLoad = (page, seconds) => page.evaluate(async s => {
  const ctx = window.__game.audio.context, cap = ctx?.renderCapacity;
  if (!cap) return null;
  const samples = [];
  const onUpdate = e => samples.push([e.averageLoad, e.peakLoad, e.underrunRatio]);
  cap.addEventListener('update', onUpdate); cap.start({ updateInterval: 1 });
  await new Promise(r => setTimeout(r, s * 1000 + 300));
  cap.stop(); cap.removeEventListener('update', onUpdate);
  if (!samples.length) return null;
  const mean = i => +(samples.reduce((a, v) => a + v[i], 0) / samples.length).toFixed(4);
  return { average: mean(0), peak: +Math.max(...samples.map(v => v[1])).toFixed(4), underruns: mean(2), samples: samples.length };
}, seconds);
