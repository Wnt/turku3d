// Selective deployed UI acceptance; no synthetic world or local server replaces the live game.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot } from '../../tools/dev/environment.mjs';
isolateBrowser();
const out = path.join(reportsRoot, 'dev-layers'); fs.mkdirSync(out, { recursive: true });
const context = await chromium.launchPersistentContext(path.join(process.env.TURKU3D_BROWSER_DIR, 'dev-layers'), {
  executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs, headless: true,
  viewport: { width: 1280, height: 720 }, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']),
});
const stop = setTimeout(() => { void context.close(); }, 480000);
try {
  const page = await context.newPage();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('https://wnt.github.io/turku-game/game/?dev=1', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.__game?.ready && window.__game?.devLayers, null, { timeout: 180000 });
  await page.locator('[data-option="multiplayer"]').click();
  await page.waitForFunction(() => !document.getElementById('dev-layers')?.hidden);
  await page.keyboard.press('F4');
  const select = page.locator('#dev-layers select'); await select.waitFor({ state: 'visible' });
  const baseline = await page.evaluate(() => ({
    ids: [...window.__game.physics.loaded].sort(),
    layers: [...window.__game.devLayers.paths].sort(),
  }));
  assert(baseline.layers.some(p => p.startsWith('Buildings/')));
  const buildings = page.locator('#dev-layers details[data-group="Buildings"] summary input');
  await buildings.uncheck();
  await page.waitForFunction(() => {
    const panel = window.__game.devLayers;
    return panel.filtered.size > 0 || [...panel.masked.keys()].some(o => o.userData.layer === 'buildings');
  });
  await buildings.check();
  await select.selectOption('first');
  await page.waitForFunction(() => [...window.__game.coarse.entries.values()].filter(e => e.model).every(e =>
    e.pass === 'first' || e.comparisonMode === 'first'), null, { timeout: 120000 });
  const first = await page.evaluate(() => [...window.__game.coarse.entries].filter(([, e]) => e.model)
    .map(([tile, e]) => ({ tile, pass: e.pass, comparison: e.comparisonMode, baseVisible: e.model.visible })));
  assert(first.length > 0); assert(first.every(e => e.pass === 'first' || !e.baseVisible));
  await page.screenshot({ path: path.join(out, 'first-pass.png') });
  await select.selectOption('full');
  await page.waitForFunction(() => [...window.__game.coarse.entries.values()].filter(e => e.model).some(e =>
    (e.pass === 'full' && e.model.visible) || e.comparisonMode === 'full'), null, { timeout: 120000 });
  await select.selectOption('auto');
  await page.waitForFunction(() => [...window.__game.coarse.entries.values()].filter(e => e.model)
    .every(e => e.model.visible && !e.comparison));
  for (const viewport of [{ width: 1280, height: 720 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    const overlaps = await page.evaluate(() => {
      const button = document.getElementById('fullscreen-toggle').getBoundingClientRect();
      return ['mp-scoreboard', 'minimap', 'status-badges', 'position-hud'].filter(id => {
        const el = document.getElementById(id); if (!el || el.hidden || !el.getClientRects().length) return false;
        const box = el.getBoundingClientRect();
        return box.width > 0 && box.height > 0 && button.left < box.right && button.right > box.left
          && button.top < box.bottom && button.bottom > box.top;
      });
    });
    assert.deepEqual(overlaps, []);
  }
  await page.keyboard.press('F4');
  assert.equal(await select.isVisible(), false);
  await page.screenshot({ path: path.join(out, 'closed-phone.png') });
  const result = { baseline, first, errors, final: await page.evaluate(() => ({
    ids: [...window.__game.physics.loaded].sort(), layers: [...window.__game.devLayers.paths].sort(),
    choices: JSON.parse(localStorage.getItem('turku3d.dev-layers.v1')),
  })) };
  assert.equal(result.final.choices.pass, 'auto');
  assert(result.final.ids.length > 0);
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} finally { clearTimeout(stop); await context.close(); }
