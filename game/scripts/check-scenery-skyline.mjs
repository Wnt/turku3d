// Headed acceptance at the user's Pääskyvuori camera pose, against deployed Pages and OCI.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const out = path.join(reportsRoot, 'scenery-skyline'); fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs,
  headless: false, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
const deadline = setTimeout(() => { void browser.close(); }, 240_000);
const report = { errors: [], requests: [], sourcePose: { E: 23463251.26, N: 6704069.95, H: 164.15, heading: 273, pitch: -17 } };
let page;
try {
  page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.bringToFront();
  const cdp = await page.context().newCDPSession(page); await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  page.on('pageerror', error => report.errors.push(error.message));
  page.on('request', request => { if (/\/coarse\/scenery/.test(request.url())) report.requests.push({ url: request.url(), at: Date.now() }); });
  await page.goto('https://wnt.github.io/turku-game/game/?dev=1&spawn=23463251.26,6704069.95,273&time=2027-07-01T08:20:00Z', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.__game?.ready, null, { timeout: 120_000 });
  await page.getByText('Multiplayer', { exact: true }).click();
  await page.waitForFunction(() => !window.__game.state().startScreen && !window.__game.state().loading, null, { timeout: 60_000 });
  await page.keyboard.press('Space'); await page.waitForTimeout(80); await page.keyboard.press('Space');
  await page.waitForFunction(() => window.__game.state().fly === true);
  await page.evaluate(() => {
    const game = window.__game, camera = window.__render.camera;
    game.physics.setCharacterEnabled(true, { x: 3751.26, y: 160, z: 430.05 });
    game.orbit.update = () => {
      camera.position.set(3751.26, 164.15, 430.05);
      camera.rotation.set(-17 * Math.PI / 180, -273 * Math.PI / 180, 0, 'YXZ');
      camera.updateMatrixWorld();
    };
    window.__skylineFrames = [];
    window.__render.afterFrame = () => {
      const now = performance.now(); window.__skylineFrames.push(now);
      const model = game.coarse.screenshotScenery.entries.get('t0_-6_5')?.model;
      if (model?.parent && window.__skylineSelected && !window.__chimneyDrawn) window.__chimneyDrawn = now;
    };
  });
  await page.waitForFunction(() => Math.abs(window.__render.camera.position.y - 164.15) < .01);
  await page.locator('#dev-layers > button').click();
  await page.screenshot({ path: path.join(out, 'before.png') });
  const select = page.locator('#dev-view-distance');
  await page.evaluate(() => { window.__skylineSelected = performance.now(); });
  await select.selectOption('6000');
  await page.waitForFunction(() => window.__chimneyDrawn, null, { timeout: 15_000 });
  report.acceptance = await page.evaluate(() => {
    const game = window.__game, camera = window.__render.camera;
    const model = game.coarse.screenshotScenery.entries.get('t0_-6_5').model;
    const points = [];
    model.updateWorldMatrix(true, true);
    model.traverse(mesh => {
      const position = mesh.geometry?.attributes.position;
      if (!position) return;
      for (let i = 0; i < position.count; i++) {
        const p = camera.position.clone().fromBufferAttribute(position, i).applyMatrix4(mesh.matrixWorld);
        if (p.y < 80 || Math.hypot(p.x + 1415.767, p.z - 1276.165) > 60) continue;
        p.project(camera); points.push([p.x, p.y, p.z]);
      }
    });
    const frames = window.__skylineFrames.filter(t => t > window.__skylineSelected);
    const ms = frames.slice(1).map((t, i) => t - frames[i]).sort((a, b) => a - b);
    return { seconds: (window.__chimneyDrawn - window.__skylineSelected) / 1000,
      heading: game.coarse.screenshotScenery.heading, progress: game.coarse.screenshotScenery.progress,
      points, source: game.coarse.screenshotScenery.sources.get('t0_-6_5'),
      frameMsP50: ms[Math.floor(ms.length / 2)], frameMsP95: ms[Math.floor(ms.length * .95)],
      renderer: { calls: window.__render.renderer.info.render.calls, triangles: window.__render.renderer.info.render.triangles } };
  });
  assert.ok(report.acceptance.seconds < 15, `Chimney arrived after ${report.acceptance.seconds}s`);
  assert.ok(report.acceptance.points.some(([x, y, z]) => Math.abs(x) < 1 && Math.abs(y) < 1 && z > -1 && z < 1), 'Chimney geometry must be inside the rendered view');
  await page.screenshot({ path: path.join(out, 'chimney-visible.png') });
  const points = report.acceptance.points;
  const x = Math.max(0, Math.min(...points.map(p => (p[0] + 1) * 800)) - 30);
  const y = Math.max(0, Math.min(...points.map(p => (1 - p[1]) * 500)) - 30);
  const right = Math.min(1600, Math.max(...points.map(p => (p[0] + 1) * 800)) + 30);
  const bottom = Math.min(1000, Math.max(...points.map(p => (1 - p[1]) * 500)) + 30);
  await page.screenshot({ path: path.join(out, 'chimney-detail.png'), clip: { x, y, width: right - x, height: bottom - y } });
  report.acceptance.chimneyVertices = points.length; delete report.acceptance.points;
  const locked = report.acceptance.heading;
  await page.evaluate(() => {
    const camera = window.__render.camera;
    window.__game.orbit.update = () => { camera.rotation.y += .01; camera.updateMatrixWorld(); };
  });
  await page.waitForTimeout(1000);
  assert.equal(await page.evaluate(() => window.__game.coarse.screenshotScenery.heading), locked);
  await select.selectOption('0');
  assert.equal(await page.evaluate(() => window.__game.coarse.screenshotScenery.progress.total), 0);
  assert.deepEqual(report.errors, []);
  report.ok = true;
} catch (error) {
  report.failure = String(error);
  if (page) { report.body = await page.locator('body').innerText().catch(() => '');
    report.state = await page.evaluate(() => ({ state: window.__game?.state?.(), scenery: window.__game?.coarse?.screenshotScenery?.progress })).catch(() => null);
    await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {}); }
  throw error;
} finally {
  clearTimeout(deadline); fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(report, null, 2));
  await browser.close(); console.log(JSON.stringify({ ...report, requests: report.requests.length }));
}
