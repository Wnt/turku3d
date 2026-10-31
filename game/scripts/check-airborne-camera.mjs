// Selective deployed-client check: real worker motion and rendered character/camera alignment.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const out = path.join(reportsRoot, 'airborne-camera'); fs.mkdirSync(out, { recursive: true });
const expected = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
const report = { expected, errors: [], phases: [] }, started = performance.now();
const browser = await chromium.launch({ executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs,
  headless: true, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
const deadline = setTimeout(() => { void browser.close(); }, 480_000);
let page;
try {
  page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  await page.addInitScript(() => Object.defineProperty(navigator, 'getGamepads', { value: () => [] }));
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  page.on('pageerror', error => report.errors.push(error.message));
  const url = 'https://wnt.github.io/turku-game/game/?dev=1&aa=fxaa';
  const until = Date.now() + 120_000;
  while (true) {
    const response = await page.request.get(`${url}&deployment=${Date.now()}`);
    const html = await response.text();
    if (html.includes(`name="turku3d-release" content="${expected}"`)) break;
    assert.ok(Date.now() < until, 'Pages must serve the deployed source before testing');
    await page.waitForTimeout(2000);
  }
  await page.goto(`${url}&deployment=${Date.now()}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.__game?.ready, null, { timeout: 180_000 });
  report.release = await page.locator('meta[name=turku3d-release]').getAttribute('content');
  assert.equal(report.release, expected);
  await page.getByText('Multiplayer', { exact: true }).click();
  await page.waitForFunction(() => !window.__game.state().startScreen && !window.__game.state().loading);
  const origin = await page.evaluate(() => window.__game.state().feet);
  const toggleFlight = async on => {
    await page.keyboard.press('Space'); await page.waitForTimeout(100); await page.keyboard.press('Space');
    await page.waitForFunction(on => window.__game.state().fly === on, on);
  };
  await toggleFlight(true);
  const placeAboveOrigin = async () => {
    await page.evaluate(([x, , z]) => window.__game.physics.setCharacterEnabled(true, { x, y: 600, z }), origin);
    await page.waitForFunction(() => window.__game.state().visualFeet[1] > 590);
  };
  await placeAboveOrigin();
  const capture = async name => {
    const samples = await page.evaluate(() => new Promise(resolve => {
      const samples = [];
      window.__render.afterFrame = () => {
        const g = window.__game, r = window.__render, state = g.state();
        const focus = r.camera.position.clone().fromArray(state.visualFeet); focus.y += 1.25;
        const distance = focus.distanceTo(r.camera.position), projected = focus.clone().project(r.camera);
        samples.push({ time: performance.now(), feet: state.visualFeet, velocity: state.vel,
          grounded: state.grounded, fly: state.fly, distance,
          offsetPixels: Math.hypot(projected.x * innerWidth / 2, projected.y * innerHeight / 2) });
        if (samples.length === 72) { window.__render.afterFrame = undefined; resolve(samples); }
        // Exercise the variable frame delivery seen while streaming without changing movement or camera code.
        else if (samples.length % 12 === 0) {
          const until = performance.now() + 45; while (performance.now() < until) { /* Main-thread hitch. */ }
        }
      };
    }));
    report.phases.push({ name, samples });
    await page.screenshot({ path: path.join(out, `${name}.png`) });
    const settled = samples.slice(12);
    assert.ok(settled.every(s => !s.grounded), `${name}: actual airborne motion required`);
    assert.ok(settled.every(s => s.offsetPixels < 0.75), `${name}: focus must stay within 0.75 CSS pixels`);
    assert.ok(settled.every(s => Math.abs(s.distance - 4.5) < 0.02), `${name}: character scale must stay stable`);
    console.log(name, 'maximum offset', Math.max(...settled.map(s => s.offsetPixels)));
    return settled;
  };
  await page.keyboard.down('KeyW');
  let samples = await capture('flight');
  assert.ok(samples.some(s => Math.hypot(s.velocity[0], s.velocity[2]) > 70), 'Normal flight must reach 72 m/s');
  await page.keyboard.down('Shift');
  samples = await capture('fast-flight');
  assert.ok(samples.some(s => Math.hypot(s.velocity[0], s.velocity[2]) > 280), 'Fast flight must reach 288 m/s');
  await page.keyboard.up('Shift'); await page.keyboard.up('KeyW');
  await placeAboveOrigin();
  await toggleFlight(false);
  await page.waitForFunction(() => window.__game.state().vel[1] < -10);
  samples = await capture('falling');
  assert.ok(samples.every(s => !s.fly && s.velocity[1] < -10), 'Real gravity-driven falling required');
  assert.deepEqual(report.errors, []);
  report.ok = true;
} catch (error) {
  report.failure = String(error);
  if (page) {
    report.body = await page.locator('body').innerText().catch(() => '');
    await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {});
  }
  throw error;
} finally {
  clearTimeout(deadline); report.seconds = (performance.now() - started) / 1000;
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
