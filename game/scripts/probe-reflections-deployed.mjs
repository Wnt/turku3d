// Feature-specific Pages check in our pinned local automation browser, never a player's device.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot, workRoot } from '../../tools/dev/environment.mjs';
isolateBrowser();
const out = path.join(reportsRoot, 'reflection-deployed'); fs.mkdirSync(out, { recursive: true });
// Upload completion precedes Pages/CDN propagation. Wait outside Chrome for the exact
// packaged entry point, within the enclosing async command's deadline.
const gameUrl = 'https://wnt.github.io/turku-game/game/';
const expected = fs.readFileSync(path.join(workRoot, 'build/game/index.html'), 'utf8').match(/assets\/main-[\w-]+\.js/)[0];
const deadline = Date.now() + 240000;
let served;
do {
  const response = await fetch(`${gameUrl}?reflection-release=${Date.now()}`, { signal: AbortSignal.timeout(5000), cache: 'no-store' });
  const html = await response.text(); served = html.match(/assets\/main-[\w-]+\.js/)?.[0];
  if (served === expected) break;
  console.log(JSON.stringify({ stage: 'await-pages', expected, served }));
  await new Promise(resolve => setTimeout(resolve, 5000));
} while (Date.now() < deadline);
assert.equal(served, expected, 'Pages must serve the uploaded bundle before measuring it');
console.log(JSON.stringify({ stage: 'pages-ready', bundle: served }));
const browser = await chromium.launch({ executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs,
  headless: true, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
try {
  const page = await browser.newPage({ viewport: { width: 960, height: 540 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await (await page.context().newCDPSession(page)).send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await page.goto(`${gameUrl}?dev=1&backend=webgpu&quality=medium&reflection-release=${Date.now()}`);
  assert.ok((await page.content()).includes(expected), 'browser loaded the uploaded entry point');
  await page.waitForFunction(() => window.__game?.ready || window.__qaError, null, { timeout: 75000 });
  assert.equal(await page.evaluate(() => window.__qaError ?? null), null);
  if (await page.locator('[data-option="new"]').count()) {
    await page.locator('[data-option="new"]').click();
    await page.getByRole('button', { name: 'Skip', exact: true }).click();
  } else await page.locator('[data-option="multiplayer"]').click();
  await page.waitForFunction(() => !window.__game.state().startScreen && !window.__game.state().loading);
  await page.evaluate(() => {
    const { renderer, camera, environment } = window.__render, scene = environment.sky.parent;
    const start = performance.now();
    window.__reflectionProbe = { start, frames: 0, passes: {}, transparent: 0, gpuErrors: [], memory: [] };
    renderer.backend.device.addEventListener('uncapturederror', e => window.__reflectionProbe.gpuErrors.push(e.error.message));
    const before = scene.onBeforeRender;
    scene.onBeforeRender = function(...args) {
      before.apply(this, args);
      if (args[2] !== camera) return;
      camera.position.set(180 + Math.sin((performance.now() - start) / 1000) * 0.6, 80, 330);
      camera.lookAt(100, 8, 100); camera.updateMatrixWorld();
    };
    const render = renderer.render;
    renderer.render = function(scene, camera) {
      if (scene.name.includes('[ Reflector ]')) {
        const p = window.__reflectionProbe;
        p.passes[camera.id] = (p.passes[camera.id] ?? 0) + 1;
      }
      return render.call(this, scene, camera);
    };
    const transparent = renderer._renderTransparents;
    renderer._renderTransparents = function(list, doubles, camera, scene, ...args) {
      if (scene.name.includes('[ Reflector ]')) window.__reflectionProbe.transparent += list.length;
      return transparent.call(this, list, doubles, camera, scene, ...args);
    };
    const after = window.__render.afterFrame;
    window.__render.afterFrame = () => {
      after?.(); const p = window.__reflectionProbe; p.frames++;
      if (p.frames % 60 === 0) p.memory.push({ ...renderer.info.memory });
    };
  });
  await page.waitForTimeout(12000);
  const result = await page.evaluate(() => ({ ...window.__reflectionProbe,
    seconds: (performance.now() - window.__reflectionProbe.start) / 1000,
    backend: window.__game.backend, gameErrors: window.__game.state().errors }));
  result.pageErrors = errors; result.bundle = expected;
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 2));
  await page.screenshot({ path: path.join(out, 'river.png') });
  console.log(JSON.stringify({ ...result, memory: result.memory.slice(-1), report: out }));
  assert.equal(result.backend, 'WebGPU'); assert.ok(result.frames > 60);
  assert.ok(Object.values(result.passes).some(count => count > 10), 'real river reflection rendered');
  for (const count of Object.values(result.passes)) assert.ok(count / result.seconds <= 14, 'slow-moving medium reflection stays near 12 Hz');
  assert.equal(result.transparent, 0, 'no blended objects enter reflection');
  assert.deepEqual(result.gpuErrors, []); assert.deepEqual(result.gameErrors, []); assert.deepEqual(errors, []);
} finally { await browser.close(); }
