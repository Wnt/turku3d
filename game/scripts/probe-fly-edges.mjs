// Exercise deployed client/worker code against one bounded local collision fixture; never build OCI tiles.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { routeCoarse, cells } from '../tests/e2e/coarseFake.ts';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const browser = await chromium.launch({ executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs,
  headless: true, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
const out = path.join(reportsRoot, 'fly-edges');
fs.mkdirSync(out, { recursive: true });
const result = { errors: [], demands: [] };
const deadline = setTimeout(() => void browser.close(), 240_000);
try {
  const page = await browser.newPage({ viewport: { width: 960, height: 540 } });
  page.setDefaultTimeout(20_000);
  await page.routeWebSocket('wss://fly.invalid/', socket => socket.close());
  page.on('console', message => { if (message.type() === 'warning' || message.type() === 'error') console.log(message.type(), message.text()); });
  page.on('pageerror', error => result.errors.push(error.message));
  page.on('request', request => {
    if (/\/coarse\/demand/.test(request.url())) result.demands.push(request.url());
  });
  const roads = { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz',
    bbox: [0, 0, 250, 250], nodes: [30, 240, 220, 240], edges: [[0, 1, 3, 190]] };
  await routeCoarse(page, { t0_0_0: { cells: cells(0, 0), roads: { nodes: 2, edges: 1, json: roads } } });
  await page.route('**/coarse/navigation.json', route => route.fulfill({ status: 200,
    contentType: 'application/json', body: JSON.stringify({ roads, addresses: {
      format: 'turku3d-addresses', version: 1, frame: 'engine-xz', origin: [77.1, 187.6], addresses: [],
    } }) }));
  await page.route(/\/coarse\/(health|demand)(\?|$)/, route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ state: 'idle', version: 1 }) }));
  await page.goto('https://wnt.github.io/turku-game/game/?dev=1&backend=webgl&quality=low&colRadius=60&mp=wss%3A%2F%2Ffly.invalid%2F', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.__game?.ready, null, { timeout: 30_000 }).catch(async error => {
    console.log(await page.evaluate(() => ({ errors: window.__game?.errors, progress: window.__game?.loadProgress?.() })));
    throw error;
  });
  await page.getByRole('option', { name: 'New game', exact: true }).click();
  await page.getByRole('button', { name: 'Skip', exact: true }).click();
  await page.waitForFunction(() => {
    const s = window.__game.state(); return !s.loading && !s.startScreen && s.grounded && s.recovery.armed;
  });
  console.log('fly-edges: gameplay started');
  const state = () => page.evaluate(() => window.__game.state());
  result.start = await state();
  const toggleFlight = async label => {
    await page.keyboard.press('Escape');
    await page.getByRole('option', { name: 'Developer', exact: true }).click();
    await page.getByRole('option', { name: label, exact: true }).click();
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
  };
  await toggleFlight('Fly mode on');
  // Start near ground height so the wall would stop flight without the worker query predicate.
  await page.keyboard.down('w');
  await page.waitForFunction(() => {
    const s = window.__game.state(), [x, , z] = s.feet;
    return x < -80 || x > 330 || z < -80 || z > 330;
  });
  await page.keyboard.up('w');
  result.outside = await state();
  console.log('fly-edges: crossed edge', result.outside.feet);
  assert.equal(result.outside.recovery.lost, null);
  assert.equal(result.outside.fly, true);
  // Descending over missing collision must remain free as well.
  await page.keyboard.down('q');
  await page.waitForFunction(y => window.__game.state().H < y - 10, result.outside.H);
  await page.keyboard.up('q');
  result.descended = await state();
  assert.ok(result.descended.H < result.outside.H - 10);
  assert.equal(result.descended.recovery.lost, null);
  result.streaming = await page.evaluate(() => {
    const g = window.__game;
    return { entries: [...g.coarse.entries.keys()], camera: g.state().camera.pos, feet: g.state().feet };
  });
  assert.ok(result.demands.some(url => {
    const tiles = new URL(url).searchParams.get('tiles') ?? '';
    return tiles.split(',').some(id => id && id !== 't0_0_0');
  }), 'generation demand follows the flying player');
  assert.ok(result.streaming.entries.some(id => id !== 't0_0_0'), 'streaming follows flight outside the fixture');
  await toggleFlight('Fly mode off');
  await page.waitForFunction(() => window.__game.state().recovery.lost !== null);
  result.ended = await state();
  assert.equal(result.ended.fly, false);
  assert.match(result.ended.recovery.lost.reason, /outside|below/);
  await page.waitForFunction(() => window.__game.state().recovery.resets > 0);
  result.recovered = await state();
  assert.equal(result.recovered.recovery.lost, null);
  assert.ok(result.recovered.feet[0] >= 0 && result.recovered.feet[0] <= 250);
  assert.ok(result.recovered.feet[2] >= 0 && result.recovered.feet[2] <= 250);
  assert.deepEqual(result.errors, []);
  console.log(JSON.stringify({ ok: true, outside: result.outside.feet, descended: result.descended.feet,
    recovered: result.recovered.feet, streaming: result.streaming.entries }));
} catch (error) {
  result.failure = String(error);
  throw error;
} finally {
  clearTimeout(deadline);
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 2));
  await browser.close();
}
