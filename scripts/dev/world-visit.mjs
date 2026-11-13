#!/usr/bin/env node
// Visits a location of the world pipeline as a player would, on any origin (the dev server or the published lab),
// and leaves stills: what `w2-play` shows as evidence that a link works.
//
//   python3 ~/turku3d/tools/host-lease/session.py run mac NAME --kind func --window-size 1600,900 --load-timeout 300 -- \
//     node scripts/dev/world-visit.mjs --url 'https://city.lab.madekivi.fi/?world=portsa' --out DIR [--mode look|play]
//
// look (default): the free camera (?world=NAME): waits for the location, takes a still, flies forward and turns with
// the keys and the mouse a visitor uses, takes stills, and checks that the camera moved.
// play: the game itself (?world=NAME&play=1): waits for the session, walks, enters the car, drives; stills and the
// player's positions from window.__game.state().
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
if (!url || !out || !process.env.BROWSER_CDP) { console.error('usage: BROWSER_CDP=... world-visit.mjs --url URL --out DIR [--mode look|play]'); process.exit(2); }
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
  if (mode === 'look') {
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
    const state = () => page.evaluate(() => { const s = window.__game.state(); return { mode: s.mode, feet: s.feet, drivenId: s.drivenId, vehicle: s.vehicle && { speed: s.vehicle.speed ?? null }, E: s.E, N: s.N, H: s.H }; });
    await page.mouse.click(400, 300);
    await page.waitForTimeout(5000);
    const a = await state();
    await still('play-1-start');
    await hold('KeyW', 3000);
    const b = await state();
    await still('play-2-walked');
    await page.keyboard.press('KeyF');
    await page.waitForTimeout(4000);
    const c = await state();
    await still('play-3-in-car');
    await hold('KeyW', 5000);
    const d = await state();
    await still('play-4-driving');
    await page.waitForTimeout(2500);
    const e = await state();
    await still('play-5-after');
    report.states = [a, b, c, d, e];
    const dist = (p, q) => Math.hypot(p.feet[0] - q.feet[0], p.feet[2] - q.feet[2]);
    report.checks.onGround = Number.isFinite(a.feet?.[1]) && Math.abs(a.feet[1] - b.feet[1]) < 3;
    report.checks.walked = dist(a, b) > 3;
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
