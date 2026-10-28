// Manual release UX evidence on real served tiles; not part of the gate or browser specs.
// Build with VITE_PUBLIC_RELEASE=1 make game-build, then run through host_slots.py browser.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';
import { gameDist, checkGameDist, isolateBrowser, port, reportsRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const repo = fileURLToPath(new URL('../../', import.meta.url));
const backend = process.env.UX_QA_BACKEND ?? 'webgpu';
const out = path.join(reportsRoot, backend === 'webgl' ? 'release-ux-webgl' : 'release-ux');
await fs.mkdir(out, { recursive: true });
const tiles = await fs.realpath(process.env.TILE_DIST ?? path.join(process.env.TURKU3D_DATA, 'live/tiles'));
const children = [];
const logs = [];
let browser, failurePage;
const errors = [];
const events = [];
const EPOCH = Date.UTC(2027, 9, 15, 5, 45);
const DAY = 1_800_000;
function child(args, env = {}) {
  const processChild = spawn(process.execPath, args, { cwd: repo, env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
  processChild.stdout.on('data', b => logs.push(String(b)));
  processChild.stderr.on('data', b => logs.push(String(b)));
  children.push(processChild);
  return processChild;
}
async function available(url) {
  const start = Date.now();
  while (Date.now() - start < 15_000) {
    try { if ((await fetch(url)).ok) return; } catch { /* Child has not bound its port yet. */ }
    await new Promise(r => setTimeout(r, 100));
  }
  throw new Error(`Server unavailable: ${url}\n${logs.join('')}`);
}
const frames = async (page, count) => {
  const first = await page.evaluate(() => window.__game.clock.frames);
  await page.waitForFunction(({ first, count }) => window.__game.clock.frames >= first + count, { first, count }, { timeout: 90_000 });
};
async function wall(page, utc, runs = false) {
  await page.evaluate(({ utc, runs }) => { window.__wallUtc = utc; window.__wallRuns = runs; window.__wallStart = performance.now(); }, { utc, runs });
  await frames(page, 32);
}
async function shot(page, name) { await page.screenshot({ path: path.join(out, `${name}.png`) }); }

try {
  child(['tools/dev/serve-dist.mjs', '--root', gameDist, '--port', String(port(1)), '--mount', `/tiles=${tiles}`]);
  child(['server/index.js'], { TURKU_MP_PORT: String(port(3)), TURKU_MP_SCORES: path.join(out, 'scores') });
  const origin = `http://localhost:${port(1)}`;
  await Promise.all([available(origin), available(`http://127.0.0.1:${port(3)}/healthz`)]);
  browser = await chromium.launch({ channel: 'chrome', headless: true,
    args: ['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist'] });
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  await context.addInitScript(utc => {
    window.__wallUtc = utc; window.__wallRuns = false; window.__wallStart = performance.now();
    Date.now = () => window.__wallUtc + (window.__wallRuns ? performance.now() - window.__wallStart : 0);
  }, EPOCH);
  const page = failurePage = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { events.push(`[${m.type()}] ${m.text()}`); if (m.type() === 'error') errors.push(m.text()); });
  let releaseWasm;
  const wasm = new Promise(resolve => { releaseWasm = resolve; });
  await page.route('**/*.wasm', async route => { await wasm; await route.continue(); });
  const query = `?backend=${backend}&tiles=/tiles/tileset.json&mp=${encodeURIComponent(`ws://127.0.0.1:${port(3)}/`)}`;
  await page.goto(`${origin}/${query}`, { waitUntil: 'commit' });
  await page.waitForFunction(() => document.querySelector('.loading-active')?.textContent.includes('Physics worker'), null, { timeout: 90_000 });
  await shot(page, 'loading-mid-load');
  const loadingActive = await page.locator('.loading-active').textContent();
  releaseWasm();
  await page.waitForFunction(() => window.__qaError || window.__game?.tiles?.errors.length ||
    window.__game?.ready && window.__game.tiles.loadedModels > 0, null, { timeout: 120_000 });
  assert.equal(await page.evaluate(() => window.__qaError ?? window.__game.tiles.errors[0] ?? null), null);
  await frames(page, 3);
  const options = await page.locator('#start-screen .menu-option').evaluateAll(rows => rows.map(row => row.dataset.option));
  assert.deepEqual(options, ['multiplayer']);
  await shot(page, 'start-menu');
  const bootLog = await page.locator('.loading-log').textContent();
  await page.locator('#start-screen [data-option="multiplayer"]').click();
  await page.waitForFunction(() => {
    const state = window.__game.state();
    return !state.startScreen && !state.loading && state.grounded && state.visModels > 0;
  }, null, { timeout: 120_000 });
  await page.locator('#loading-screen').waitFor({ state: 'hidden', timeout: 30_000 });
  await page.evaluate(() => { window.__game.orbit.yaw = -2.3; window.__game.orbit.pitch = 0.08; window.__game.orbit.distance = 5.5; });
  await frames(page, 30);
  await page.waitForFunction(() => window.__game.state().visModels > 0, null, { timeout: 90_000 });
  const nick = await page.evaluate(() => window.__game.state().multiplayer.nick);
  assert.equal(await page.locator('#hud').textContent(), nick);
  assert.equal(await page.locator('#time-control').count(), 0);
  await shot(page, 'hud-morning');
  await page.keyboard.press('F3'); await frames(page, 3);
  assert.match(await page.locator('#hud').textContent(), /fps.*ms\/frame/);
  await shot(page, 'debug-f3');
  await page.keyboard.press('F3'); await frames(page, 3);
  assert.equal(await page.locator('#hud').textContent(), nick);
  await wall(page, EPOCH + DAY * 0.85);
  await shot(page, 'hud-afternoon');
  const lighting = [];
  for (const [name, phase] of [['evening', DAY], ['transition-quarter', DAY + 2500],
    ['transition-middle', DAY + 5000], ['transition-three-quarter', DAY + 7500], ['morning-return', DAY + 10_000]]) {
    await wall(page, EPOCH + phase);
    await shot(page, name);
    lighting.push(await page.evaluate(name => ({ name, clock: window.__game.dayClock.snapshot(),
      elevation: window.__render.environment.sunElevation, intensity: window.__render.environment.sun.intensity }), name));
  }
  // Two separate uses of six of the eight foot prompts. Each held input contributes only one press start.
  for (let i = 0; i < 2; i++) {
    await page.keyboard.down('KeyW'); await page.keyboard.down('ShiftLeft'); await frames(page, 3);
    await page.keyboard.up('KeyW'); await page.keyboard.up('ShiftLeft');
    await page.keyboard.press('Space'); await frames(page, 3);
    await page.keyboard.press('KeyC'); await page.keyboard.press('KeyM'); await frames(page, 3);
    await page.mouse.move(1050, 300); await page.mouse.down(); await page.mouse.move(1060, 305); await frames(page, 3);
    await page.mouse.up(); await frames(page, 30);
  }
  await page.waitForFunction(() => document.getElementById('help').hidden, null, { timeout: 15_000 });
  const learned = await page.evaluate(() => JSON.parse(localStorage.getItem('turku3d.controls-help.v1')));
  assert.ok(learned.foot.elapsedMs >= 60_000 || Object.values(learned.foot.counts).filter(n => n >= 2).length >= 6);
  await shot(page, 'help-learned');
  // New fly mode shows its unlearned prompts; returning to foot retains the learned state.
  await page.keyboard.press('Space'); await frames(page, 2); await page.keyboard.press('Space'); await frames(page, 3);
  assert.equal(await page.evaluate(() => window.__game.state().fly), true);
  assert.equal(await page.locator('#help').evaluate(el => el.hidden), false);
  await shot(page, 'help-new-fly-mode');
  await frames(page, 30);
  await page.keyboard.press('Space'); await frames(page, 2); await page.keyboard.press('Space'); await frames(page, 3);
  assert.equal(await page.evaluate(() => window.__game.state().fly), false);
  assert.equal(await page.locator('#help').evaluate(el => el.hidden), true);
  const perf = {};
  for (const [name, phase] of [['daylight', DAY / 2], ['transition', DAY + 1000]]) {
    await wall(page, EPOCH + phase, true);
    await page.evaluate(() => window.__render.perf.reset());
    await frames(page, 180);
    await page.evaluate(() => window.__render.perf.flush());
    perf[name] = await page.evaluate(() => ({ frame: window.__perf, clock: window.__game.dayClock.snapshot() }));
  }
  // Compare the existing shadow passes at the same light pose with a fixed clock.
  const iblCostMs = await page.evaluate(() => {
    const start = performance.now(); window.__render.environment.updateIBL(); return performance.now() - start;
  });
  await page.evaluate(() => window.__render.setTime('2027-10-15T13:07:30+03:00'));
  await frames(page, 30);
  await page.evaluate(() => window.__render.perf.reset());
  await frames(page, 180);
  await page.evaluate(() => window.__render.perf.flush());
  perf.fixed = await page.evaluate(() => ({ frame: window.__perf, clock: window.__game.dayClock.snapshot() }));
  // ?debug defaults visible, and an explicit QA time survives starting a game.
  const debugPage = await context.newPage();
  await debugPage.goto(`${origin}/${query}&debug&time=${encodeURIComponent('2027-10-15T14:00:00+03:00')}`);
  await debugPage.waitForFunction(() => window.__game?.ready, null, { timeout: 120_000 });
  assert.equal(await debugPage.locator('body').evaluate(el => el.classList.contains('debug-hud')), true);
  await debugPage.locator('#start-screen [data-option="multiplayer"]').click();
  await debugPage.waitForFunction(() => !window.__game.state().loading && !window.__game.state().startScreen, null, { timeout: 120_000 });
  assert.equal(await debugPage.locator('#help').evaluate(el => el.hidden), true);
  assert.equal(await debugPage.evaluate(() => window.__game.dayClock.snapshot().cycling), false);
  assert.equal(await debugPage.evaluate(() => window.__game.dayClock.snapshot().time), '2027-10-15T11:00:00.000Z');
  child(['tools/dev/serve-dist.mjs', '--root', checkGameDist, '--port', String(port()), '--mount', `/tiles=${tiles}`]);
  await available(`http://localhost:${port()}`);
  const localPage = await context.newPage();
  localPage.on('pageerror', e => errors.push(e.message));
  await localPage.goto(`http://localhost:${port()}/?backend=${backend}&tiles=/tiles/tileset.json`);
  await localPage.waitForFunction(() => window.__game?.ready, null, { timeout: 120_000 });
  const localOptions = await localPage.locator('#start-screen .menu-option').evaluateAll(rows => rows.map(row => row.dataset.option));
  for (const option of ['new', 'load', 'multiplayer', 'scores']) assert.ok(localOptions.includes(option));
  assert.equal(await localPage.locator('body').evaluate(el => el.classList.contains('debug-hud')), true);
  await shot(localPage, 'localhost-menu');
  errors.push(...await localPage.evaluate(() => window.__game.errors));
  errors.push(...await debugPage.evaluate(() => window.__game.errors));
  const streaming = await page.evaluate(() => ({ progress: window.__game.tiles.tiles.loadProgress, groundPending: window.__game.materials?.ground?.pending }));
  const report = { backend, tiles, localOptions, options, streaming, nick, loadingActive, bootLog, lighting, learned, perf, iblCostMs, errors };
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ out, localOptions, options, nick, lighting, perf, iblCostMs, errors }, null, 2));
} catch (error) {
  const state = await failurePage?.evaluate(() => ({ ready: window.__game?.ready, error: window.__qaError,
    state: window.__game?.state?.(), tiles: window.__game?.tiles?.errors, progress: window.__game?.tiles?.tiles?.loadProgress, ground: window.__game?.materials?.ground?.pending,
    active: document.querySelector('.loading-active')?.textContent, log: document.querySelector('.loading-log')?.textContent })).catch(() => null);
  await fs.writeFile(path.join(out, 'failure.json'), JSON.stringify({ message: String(error), errors, events, state }, null, 2));
  await failurePage?.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {});
  throw error;
} finally {
  await browser?.close();
  for (const proc of children) proc.kill('SIGTERM');
  await fs.writeFile(path.join(out, 'servers.log'), logs.join(''));
}
