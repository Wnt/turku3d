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
const report = { errors: [], requests: [], responses: [], sourcePose: { E: 23463251.26, N: 6704069.95, H: 164.15, heading: 273, pitch: -17 } };
let page;
try {
  page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.bringToFront();
  page.on('response', async response => {
    if (!response.url().includes('017ee9a8')) return;
    report.responses.push({ url: response.url(), status: response.status(), at: Date.now(),
      serviceWorker: response.fromServiceWorker(), body: response.url().endsWith('.json') ? await response.text().catch(() => '') : undefined });
  });
  const cdp = await page.context().newCDPSession(page); await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  page.on('pageerror', error => report.errors.push(error.message));
  page.on('request', request => { if (/\/coarse\/scenery/.test(request.url())) report.requests.push({ url: request.url(), at: Date.now() }); });
  const readyUntil = Date.now() + 90_000;
  do {
    await page.goto('https://wnt.github.io/turku-game/game/?dev=1&spawn=23463251.26,6704069.95,273&time=2027-07-01T08:20:00Z&deployment=' + Date.now(), { waitUntil: 'domcontentloaded' });
    report.release = await page.locator('meta[name=turku3d-release]').getAttribute('content');
    if (!process.env.SKYLINE_EXPECT_RELEASE || report.release === process.env.SKYLINE_EXPECT_RELEASE) break;
    assert.ok(Date.now() < readyUntil, `Pages still serves ${report.release}`);
    await page.waitForTimeout(3000);
  } while (true);
  await page.waitForFunction(() => window.__game?.ready, null, { timeout: 120_000 });
  // A unique document URL bypasses the previous Pages HTML while assets remain content-addressed.
  report.release = await page.locator('meta[name=turku3d-release]').getAttribute('content');
  assert.ok(await page.locator('#dev-view-distance option[value="6000"]').count(),
    `Pages release ${report.release} must contain the 6 km scenery control before timing starts`);
  console.log(`Loaded Pages release ${report.release}`);
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
      const progress = game.coarse.screenshotScenery.progress;
      if (window.__skylineSelected && progress.total > 0 && progress.loaded === progress.total && !window.__sceneryCompleteAt)
        window.__sceneryCompleteAt = now;
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
  await page.waitForFunction(() => performance.now() - window.__skylineSelected >= 15_000);
  report.at15Seconds = await page.evaluate(() => ({ ...window.__game.coarse.screenshotScenery.progress,
    fullViewSeconds: window.__sceneryCompleteAt ? (window.__sceneryCompleteAt - window.__skylineSelected) / 1000 : null }));
  await page.screenshot({ path: path.join(out, 'chimney-visible.png') });
  const points = report.acceptance.points;
  const x = Math.max(0, Math.min(...points.map(p => (p[0] + 1) * 800)) - 30);
  const y = Math.max(0, Math.min(...points.map(p => (1 - p[1]) * 500)) - 30);
  const right = Math.min(1600, Math.max(...points.map(p => (p[0] + 1) * 800)) + 30);
  const bottom = Math.min(1000, Math.max(...points.map(p => (1 - p[1]) * 500)) + 30);
  await page.screenshot({ path: path.join(out, 'chimney-detail.png'), clip: { x, y, width: right - x, height: bottom - y } });
  report.acceptance.chimneyVertices = points.length; delete report.acceptance.points;
  await page.evaluate(() => {
    const scenery = window.__game.coarse.screenshotScenery;
    window.__originalChimney = scenery.entries.get('t0_-6_5').model.uuid;
    window.__originalFootprint = [...scenery.footprint];
    const camera = window.__render.camera;
    window.__skylineHeading = 333;
    window.__game.orbit.update = () => {
      camera.rotation.set(-17 * Math.PI / 180, -window.__skylineHeading * Math.PI / 180, 0, 'YXZ');
      camera.updateMatrixWorld();
    };
  });
  await page.waitForFunction(() => Math.abs(window.__game.coarse.screenshotScenery.heading - 333) < .1
    && !window.__game.coarse.screenshotScenery.footprint.includes('t0_-6_5'));
  await page.waitForTimeout(300);
  await page.evaluate(() => { window.__skylineHeading = 273; });
  await page.waitForFunction(() => Math.abs(window.__game.coarse.screenshotScenery.heading - 273) < .1
    && window.__game.coarse.screenshotScenery.footprint.includes('t0_-6_5'));
  assert.equal(await page.evaluate(() => window.__game.coarse.screenshotScenery.entries.get('t0_-6_5')?.model?.uuid),
    await page.evaluate(() => window.__originalChimney), 'Brief look-back must reuse the chimney model');
  await page.evaluate(() => { window.__skylineHeading = 333; });
  await page.waitForFunction(() => {
    const scenery = window.__game.coarse.screenshotScenery;
    return Math.abs(scenery.heading - 333) < .1 && scenery.footprint.some(id =>
      !window.__originalFootprint.includes(id) && scenery.entries.get(id)?.model?.parent);
  }, null, { timeout: 15_000 });
  report.dynamic = await page.evaluate(() => {
    const scenery = window.__game.coarse.screenshotScenery;
    return { heading: scenery.heading, newlyVisibleLoaded: scenery.footprint.filter(id =>
      !window.__originalFootprint.includes(id) && scenery.entries.get(id)?.model?.parent).length,
      progress: scenery.progress, lookBackReusedModel: true };
  });
  await page.screenshot({ path: path.join(out, 'dynamic-turn.png') });
  await select.selectOption('0');
  assert.equal(await page.evaluate(() => window.__game.coarse.screenshotScenery.progress.total), 0);
  assert.deepEqual(report.errors, []);
  report.ok = true;
} catch (error) {
  report.failure = String(error);
  if (page) { report.body = await page.locator('body').innerText().catch(() => '');
    report.state = await page.evaluate(() => ({ fly: window.__game?.state?.().fly, scenery: window.__game?.coarse?.screenshotScenery?.progress, chimney: window.__game?.coarse?.screenshotScenery?.entries.get('t0_-6_5')?.error })).catch(() => null);
    await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {}); }
  throw error;
} finally {
  clearTimeout(deadline); fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(report, null, 2));
  await browser.close(); console.log(JSON.stringify({ ...report, requests: report.requests.length }));
}
