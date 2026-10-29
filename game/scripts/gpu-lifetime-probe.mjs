// Real streamed world, all render passes, repeated semantic layer and first/full toggles.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const [url = 'https://wnt.github.io/turku-game/game/?dev=1&backend=webgpu', label = 'gpu-lifetime', secondsArg = '180'] = process.argv.slice(2);
const seconds = Number(secondsArg), out = path.join(reportsRoot, label);
fs.mkdirSync(out, { recursive: true });
const context = await chromium.launchPersistentContext(path.join(process.env.TURKU3D_BROWSER_DIR, label), {
  executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs, headless: true,
  viewport: { width: 960, height: 540 }, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']),
});
const hardStop = setTimeout(() => void context.close(), (seconds + 160) * 1000);
const consoleErrors = [], pageErrors = [];
let result;
try {
  const page = await context.newPage();
  await (await context.newCDPSession(page)).send('Emulation.setFocusEmulationEnabled', { enabled: true });
  page.on('console', m => { if (/GPUValidationError|used in submit while destroyed|index count of 0|too many warnings/i.test(m.text())) consoleErrors.push(m.text()); });
  page.on('pageerror', e => pageErrors.push(e.message));
  if (['localhost', '127.0.0.1'].includes(new URL(url).hostname)) await page.route('**/coarse/**', async route => {
    const request = new URL(route.request().url());
    const pathname = request.pathname === '/coarse/status' ? '/coarse/health' : request.pathname;
    try {
      const response = await route.fetch({ url: `https://turku.lab.madekivi.fi${pathname}${request.search}`, timeout: 20000 });
      await route.fulfill({ response });
    } catch { await route.fulfill({ status: 503, json: { state: 'offline' } }); }
  });
  await page.addInitScript(() => {
    localStorage.clear(); sessionStorage.clear();
    window.__gpuErrors = [];
    const request = GPUAdapter.prototype.requestDevice;
    GPUAdapter.prototype.requestDevice = async function(...args) {
      const device = await request.apply(this, args);
      device.addEventListener('uncapturederror', e => window.__gpuErrors.push({ type: e.error.constructor.name, message: e.error.message }));
      return device;
    };
  });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.__game?.ready || window.__qaError, null, { timeout: 130000 });
  assert.equal(await page.evaluate(() => window.__qaError ?? null), null);
  assert.equal(await page.evaluate(() => window.__game.backend), 'WebGPU');
  if (await page.locator('[data-option="new"]').count()) {
    await page.locator('[data-option="new"]').click();
    await page.getByRole('button', { name: 'Skip', exact: true }).click();
  } else await page.locator('[data-option="multiplayer"]').click();
  await page.waitForFunction(() => !window.__game.state().startScreen && !window.__game.state().loading);
  await page.evaluate(() => {
    const g = window.__game, coarse = g.coarse, camera = window.__render.camera;
    const scene = g.tiles.models.values().next().value.parent;
    const started = performance.now(), start = g.state().feet;
    window.__lifetime = { started, swaps: [], toggles: 0, samples: [], start, frames: 0 };
    const apply = coarse.applyFragment.bind(coarse);
    coarse.applyFragment = async (id, e, fragment) => {
      const before = e.model;
      await apply(id, e, fragment);
      if (e.model && before !== e.model) window.__lifetime.swaps.push({ id, pass: e.pass, upgrade: !!before });
    };
    // The fixed-speed camera/streaming corridor isolates rendering from street-navigation failures.
    const pose = () => {
      const phase = (performance.now() - started) / 1000 % 90;
      return { x: start[0] - Math.min(phase, 90 - phase) * 14, z: start[2] };
    };
    const update = coarse.update.bind(coarse), stream = g.streamer.update.bind(g.streamer);
    coarse.setRouteHint(null);
    coarse.update = () => { const p = pose(); update(p.x, p.z, performance.now()); };
    g.streamer.update = () => { const p = pose(); stream(p.x, p.z, false, -14, 0); };
    const before = scene.onBeforeRender;
    scene.onBeforeRender = function(...args) {
      before.apply(this, args);
      if (args[2] !== camera) return;
      const p = pose(); camera.position.set(p.x + 6, 18, p.z + 5); camera.lookAt(p.x - 25, 8, p.z); camera.updateMatrixWorld();
    };
    const after = window.__render.afterFrame;
    window.__render.afterFrame = () => { after?.(); window.__lifetime.frames++; };
    g.devLayers.toggle();
  });
  console.log(JSON.stringify({ label, stage: 'streaming', seconds }));
  const started = Date.now();
  for (let i = 0; Date.now() - started < seconds * 1000; i++) {
    await page.evaluate(i => {
      const panel = document.getElementById('dev-layers');
      for (const details of panel.querySelectorAll('details')) details.open = true;
      const groups = [...panel.querySelectorAll('details')].filter(d => ['Buildings', 'Vegetation', 'Props'].includes(d.dataset.group));
      for (const group of groups) {
        const boxes = [...group.querySelectorAll('input')];
        const box = i % 3 === 0 ? boxes[0] : boxes[1 + (i % Math.max(1, boxes.length - 1))];
        if (box) { box.checked = !box.checked; box.dispatchEvent(new Event('change')); window.__lifetime.toggles++; }
      }
      // Exercise comparison retirement as well as ordinary streamed first -> full upgrades.
      if (i % 12 === 0) window.__game.coarse.setVisualPass(['first', 'full', 'auto'][(i / 12) % 3]);
      window.__lifetime.samples.push({ at: performance.now(), models: window.__game.tiles.models.size,
        buffers: window.__render.renderer.info.memory.geometries, errors: window.__gpuErrors.length });
    }, i);
    await page.waitForTimeout(1000);
  }
  result = await page.evaluate(() => ({ ...window.__lifetime, seconds: (performance.now() - window.__lifetime.started) / 1000,
    gpuErrors: window.__gpuErrors, gameErrors: window.__game.state().errors }));
  result.consoleErrors = consoleErrors; result.pageErrors = pageErrors;
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 2));
  await page.screenshot({ path: path.join(out, 'end.png'), timeout: 5000 }).catch(error => console.log(`Optional screenshot: ${error.message}`));
  console.log(JSON.stringify({ report: out, seconds: result.seconds, frames: result.frames, toggles: result.toggles,
    swaps: result.swaps.length, upgrades: result.swaps.filter(s => s.upgrade).length,
    gpuErrors: result.gpuErrors.length, consoleErrors: consoleErrors.length, pageErrors }));
  assert.ok(result.frames > 60, 'world rendered throughout the probe');
  assert.ok(result.toggles >= 10, 'semantic tree toggles exercised');
  assert.ok(result.swaps.length > 0, 'real streamed tile publication exercised');
  if (seconds >= 180) assert.ok(result.swaps.some(s => s.upgrade && s.pass === 'full'), 'real full-pass upgrade exercised');
  assert.equal(result.gpuErrors.length, 0, `uncaptured GPU errors; first: ${result.gpuErrors[0]?.message}`);
  assert.equal(consoleErrors.length, 0, `GPU validation/empty-draw warnings; first: ${consoleErrors[0]}`);
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(result.gameErrors, []);
} finally {
  clearTimeout(hardStop);
  await context.close();
}
