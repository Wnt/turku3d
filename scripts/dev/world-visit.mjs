#!/usr/bin/env node
// Visits a location of the world pipeline as a player would, on any origin (the dev server or the published lab),
// and leaves stills: what `w2-play` shows as evidence that a link works.
//
//   python3 ~/turku3d/tools/host-lease/session.py run mac NAME --kind func --window-size 1600,900 --load-timeout 300 -- \
//     node scripts/dev/world-visit.mjs --url 'https://city.lab.madekivi.fi/?world=portsa' --out DIR [--mode look|play|edge|page]
//
// look (default): the free camera (?world=NAME): waits for the location, takes a still, flies forward and turns with
// the keys and the mouse a visitor uses, takes stills, and checks that the camera moved.
// play: the game itself (?world=NAME&play=1): waits for the session, walks, enters the car, drives; stills and the
// player's positions from window.__game.state().
// edge: the game, then the car is put 45 m inside the east and the north edge of the location's collision and driven
// straight at it for 12 s: where it stops, whether it stays on the ground, stills of what the edge looks like.
// page: any page of the site (the start page, the old build): final address, text, links and a still (--wait SECONDS).
// The browser is the leased one (BROWSER_CDP). Exit 0 when every check held; report.json lists them.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(path.join(repo, 'game/package.json'))('playwright');
const argv = process.argv.slice(2);
const opt = (name, fallback) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : fallback; };
const url = opt('--url'), out = opt('--out'), mode = opt('--mode', 'look'), timeout = Number(opt('--ready-timeout', 300)) * 1000;
if (!url || !out || !process.env.BROWSER_CDP) { console.error('usage: BROWSER_CDP=... world-visit.mjs --url URL --out DIR [--mode look|play|page]'); process.exit(2); }
fs.mkdirSync(out, { recursive: true });

const report = { url, mode, started: new Date().toISOString(), checks: {}, stills: [], console: [], failed: [], notes: [] };
const browser = await chromium.connectOverCDP(process.env.BROWSER_CDP);
const context = browser.contexts()[0] ?? await browser.newContext();
const page = await context.newPage();
page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') report.console.push(`${m.type()}: ${m.text().slice(0, 300)}`); });
page.on('pageerror', e => report.console.push(`pageerror: ${String(e).slice(0, 300)}`));
page.on('requestfailed', r => report.failed.push(`${r.failure()?.errorText} ${r.url().slice(0, 200)}`));
page.on('response', r => { if (r.status() >= 400) report.failed.push(`${r.status()} ${r.url().slice(0, 200)}`); });
const still = async name => { const file = path.join(out, `${name}.png`); await page.screenshot({ path: file }); report.stills.push(file); console.error(`[visit] still ${file}`); };
const hold = async (key, ms) => { await page.keyboard.down(key); await page.waitForTimeout(ms); await page.keyboard.up(key); };
const finish = async code => {
  report.ended = new Date().toISOString();
  fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 1) + '\n');
  console.log(JSON.stringify({ checks: report.checks, stills: report.stills.length, consoleErrors: report.console.length, failedRequests: report.failed.length, notes: report.notes }));
  await page.close().catch(() => {});
  await browser.close().catch(() => {});
  process.exit(code);
};

const t0 = Date.now();
try {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  if (mode === 'page') {
    // Any page of the site (the start page, the old build): where the address led, what it shows.
    await page.waitForTimeout(Number(opt('--wait', 8)) * 1000);
    report.finalUrl = page.url();
    report.title = await page.title();
    report.text = (await page.evaluate(() => document.body.innerText)).slice(0, 1500);
    report.links = await page.evaluate(() => [...document.querySelectorAll('a')].map(a => a.href).slice(0, 60));
    report.checks.loaded = !!report.text;
    await still('page');
  } else if (mode === 'look') {
    await page.waitForFunction(() => /entities/.test(document.querySelector('#preview-status')?.textContent ?? '') || !!window.__qaError,
      null, { timeout, polling: 500 });
    report.loadSeconds = (Date.now() - t0) / 1000;
    report.status = await page.evaluate(() => document.querySelector('#preview-status')?.textContent ?? '');
    report.checks.loaded = /entities/.test(report.status) && !await page.evaluate(() => window.__qaError);
    await page.waitForTimeout(6000);
    const pose = () => page.evaluate(() => window.__qaPose ?? null);
    const first = await pose();
    await still('look-1-start');
    await hold('KeyW', 2500);
    await page.waitForTimeout(1500);
    const second = await pose();
    await still('look-2-forward');
    const box = page.viewportSize() ?? { width: 1600, height: 900 };
    await page.mouse.move(box.width / 2, box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.width / 2 + 420, box.height / 2 + 40, { steps: 12 });
    await page.mouse.up();
    await hold('KeyQ', 700);
    await page.waitForTimeout(1500);
    const third = await pose();
    await still('look-3-turned');
    report.poses = [first, second, third];
    report.checks.moved = !!second && (!first || Math.hypot(second.E - first.E, second.N - first.N) > 5);
    report.checks.turned = !!third && !!second && Math.abs(third.yaw - second.yaw) > 20;
  } else {
    await page.waitForFunction(() => window.__game?.state?.().mode && window.__game.state().mode !== 'loading' || !!document.querySelector('.fatal, #fatal'),
      null, { timeout, polling: 500 });
    report.loadSeconds = (Date.now() - t0) / 1000;
    const state = () => page.evaluate(() => {
      const s = window.__game.state(), cars = window.__game.physics?.latest?.vehicles ?? [];
      return { mode: s.mode, feet: s.feet, drivenId: s.drivenId, vehicle: s.vehicle && { speed: s.vehicle.speed ?? null }, E: s.E, N: s.N, H: s.H,
        fps: /(\d+) fps/.exec(document.body.innerText)?.[1] ?? null, car: JSON.stringify(cars[0] ?? null).slice(0, 240) };
    });
    // The start menu, as scene-capture.mjs passes it: New game, then wait for the session.
    await page.waitForFunction(() => window.__game.state().startScreen !== undefined || window.__game.state().loading === false, null, { timeout: 30000 }).catch(() => {});
    if (await page.evaluate(() => window.__game.state().startScreen)) { await page.getByText('New game', { exact: true }).click(); report.notes.push('start menu: New game'); }
    // A new game opens with the story's intro pages once the player is placed: skip them, as a player may.
    const playing = () => page.evaluate(() => { const s = window.__game.state(); return !s.startScreen && !s.loading; });
    for (const began = Date.now(); !await playing();) {
      if (Date.now() - began > 150000) throw new Error('the new game did not start within 150 s');
      // With a save in this browser profile the menu asks once more; then the intro pages.
      for (const label of ['Start a new game', 'Skip']) {
        const button = page.getByText(label, { exact: true });
        if (await button.isVisible().catch(() => false)) { await button.click(); report.notes.push(`${label} after ${Math.round((Date.now() - began) / 1000)} s`); }
      }
      await page.waitForTimeout(500);
    }
    report.session = await page.evaluate(() => { const { startScreen, loading, paused, mode, inputSource, tilesSettled } = window.__game.state(); return { startScreen, loading, paused, mode, inputSource, tilesSettled }; });
    await page.waitForTimeout(3000);
    if (mode === 'edge') {
      const name = new URL(url).searchParams.get('world');
      const cells = await page.evaluate(async name => Object.keys((await (await fetch(new URL(`world/locations/${name}.json`, location.href))).json()).collision ?? {}), name);
      const ij = cells.map(id => id.split('_').slice(1).map(Number)), CELL = 62.5;
      const east = (Math.max(...ij.map(c => c[0])) + 1) * CELL, north = Math.min(...ij.map(c => c[1])) * CELL;
      const start = (await state()).feet;
      await page.evaluate(() => window.__game.enterVehicle('player'));
      report.edges = [];
      // Forward is (-sin yaw, -cos yaw): east is yaw -90 degrees, north (towards -z) yaw 0.
      for (const edge of [{ name: 'east', yaw: -Math.PI / 2, at: k => [east - 45, start[2] + k], out: p => p[0] - east },
        { name: 'north', yaw: 0, at: k => [start[0] + k, north + 45], out: p => north - p[2] }]) {
        let placed = null;
        for (const k of Array.from({ length: 41 }, (_, n) => (n % 2 ? 1 : -1) * Math.ceil(n / 2) * 15)) {
          const [x, z] = edge.at(k);
          if (await page.evaluate(([x, z, yaw]) => window.__game.relocateVehicle(x, z, yaw), [x, z, edge.yaw])) { placed = [x, z]; break; }
        }
        if (!placed) { report.edges.push({ edge: edge.name, placed: null }); continue; }
        await page.waitForTimeout(1500);
        const before = await state();
        await still(`edge-${edge.name}-1-before`);
        const track = [];
        await page.keyboard.down('KeyW');
        for (let t = 0; t < 12; t++) { await page.waitForTimeout(1000); const s = await state(); track.push([Math.round(edge.out(s.feet) * 10) / 10, Math.round(s.feet[1] * 10) / 10]); }
        await page.keyboard.up('KeyW');
        await page.waitForTimeout(1500);
        const after = await state();
        await still(`edge-${edge.name}-2-at`);
        // Metres beyond the edge of the collision (negative: inside), and height, each second of the drive.
        report.edges.push({ edge: edge.name, placed, track, beyond: edge.out(after.feet), height: [before.feet[1], after.feet[1]] });
      }
      report.checks.placed = report.edges.every(e => e.placed);
      report.checks.stoppedAtEdge = report.edges.every(e => e.placed && e.beyond < 4);
      report.checks.reachedEdge = report.edges.every(e => e.placed && e.beyond > -12);
      report.checks.stayedOnGround = report.edges.every(e => e.placed && Math.abs(e.height[1] - e.height[0]) < 15);
      await finish(Object.values(report.checks).every(Boolean) ? 0 : 1);
    }
    const a = await state();
    await still('play-1-start');
    await hold('KeyW', 2500);
    const b = await state();
    await still('play-2-walked');
    // Walk to the car with the keys whose direction (relative to the camera) points at it, then get in.
    const toCar = () => page.evaluate(() => {
      const s = window.__game.state(), car = (window.__game.physics?.latest?.vehicles ?? [])[0];
      // On foot the camera stands behind the player: its ground direction to the feet is "forward".
      return car ? { dx: car.pos.x - s.feet[0], dz: car.pos.z - s.feet[2], fx: s.feet[0] - s.camera.pos[0], fz: s.feet[2] - s.camera.pos[2] } : null;
    });
    const combos = { KeyW: [0, 1], KeyS: [0, -1], KeyD: [1, 0], KeyA: [-1, 0], 'KeyW+KeyD': [1, 1], 'KeyW+KeyA': [-1, 1], 'KeyS+KeyD': [1, -1], 'KeyS+KeyA': [-1, -1] };
    for (let step = 0; step < 40; step++) {
      const t = await toCar();
      if (!t || Math.hypot(t.dx, t.dz) < 2.6) break;
      const f = Math.hypot(t.fx, t.fz) || 1, forward = [t.fx / f, t.fz / f], right = [-forward[1], forward[0]], n = Math.hypot(t.dx, t.dz);
      let best = 'KeyW', score = -Infinity;
      for (const [keys, [r, f]] of Object.entries(combos)) {
        const x = right[0] * r + forward[0] * f, z = right[1] * r + forward[1] * f, dot = (x * t.dx + z * t.dz) / (Math.hypot(x, z) * n);
        if (dot > score) { score = dot; best = keys; }
      }
      const keys = best.split('+');
      for (const key of keys) await page.keyboard.down(key);
      await page.waitForTimeout(250);
      for (const key of keys) await page.keyboard.up(key);
    }
    report.carDistance = await toCar().then(t => t && Math.hypot(t.dx, t.dz));
    await page.keyboard.press('KeyF');
    await page.waitForTimeout(4000);
    const c = await state();
    await still('play-3-in-car');
    await hold('KeyW', 6000);
    const d = await state();
    await still('play-4-driving');
    await page.waitForTimeout(2500);
    const e = await state();
    await still('play-5-after');
    report.states = [a, b, c, d, e];
    const dist = (p, q) => Math.hypot(p.feet[0] - q.feet[0], p.feet[2] - q.feet[2]);
    report.checks.onGround = Number.isFinite(a.feet?.[1]) && Math.abs(a.feet[1] - b.feet[1]) < 3;
    report.checks.walked = dist(a, b) > 3;
    report.moved = { walked: dist(a, b), drove: dist(c, d) };
    report.checks.enteredCar = !!c.drivenId;
    report.checks.drove = !!d.drivenId && dist(c, d) > 8;
    report.checks.stayedInWorld = Number.isFinite(e.feet?.[1]) && e.feet[1] > a.feet[1] - 20;
  }
} catch (error) {
  report.notes.push(`failed: ${String(error).slice(0, 400)}`);
  await still('failure').catch(() => {});
  await finish(1);
}
await finish(Object.values(report.checks).every(Boolean) ? 0 : 1);
