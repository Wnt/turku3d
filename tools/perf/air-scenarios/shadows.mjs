// Manual real-tile inspection. Run through host_slots.py browser; screenshots and audit stay in the sandbox.
import { chromium } from '../../../game/node_modules/playwright-core/index.mjs';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../dev/chrome-args.mjs';
import { isolateBrowser, workRoot } from '../../dev/environment.mjs';
import fs from 'node:fs';
import path from 'node:path';
isolateBrowser();
const [base = 'http://localhost:5244/', output = path.join(workRoot, 'reports/air-render/shadow-sanity')] = process.argv.slice(2);
fs.mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ executablePath: chromeExecutable(), headless: true, ignoreDefaultArgs: chromeIgnoreDefaultArgs,
  args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
const results = [], errors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 1000 }, deviceScaleFactor: 1 });
  page.on('pageerror', error => errors.push(error.message));
  for (const [id, x, z] of [['t0_0_0', 125, 125], ['t0_0_1', 125, 375]]) {
    const url = new URL(base);
    url.search = new URLSearchParams({ tiles: '/tiles/tileset.json', cam: `${23459500 + x},${6704500 - z},450,0,-90,50`,
      qa: '1', backend: 'webgpu', time: '2026-10-15T11:00:00Z' });
    await page.goto(url.href);
    await page.waitForFunction(() => window.__game?.tiles);
    await page.evaluate(([x, z]) => window.__game.tiles.setFocus(x, z, 0), [x, z]);
    await page.waitForFunction(() => window.__qaReady || window.__qaError, null, { timeout: 180000 });
    const audit = await page.evaluate(() => {
      const rows = [];
      for (const model of window.__game.tiles.models) model.traverse(root => {
        if (root.userData.shadowTriangles === undefined) return;
        let triangles = 0, chunks = 0, outside = 0, bytes = 0;
        root.traverse(mesh => {
          const geometry = mesh.geometry, p = geometry.attributes.position, index = geometry.index;
          chunks++; triangles += index.count / 3; bytes += p.array.byteLength + index.array.byteLength;
          const sphere = geometry.boundingSphere;
          for (let i = 0; i < index.count; i++) {
            const n = index.getX(i);
            if (Math.hypot(p.getX(n) - sphere.center.x, p.getY(n) - sphere.center.y, p.getZ(n) - sphere.center.z) > sphere.radius + 1e-6) outside++;
          }
        });
        rows.push({ model: model.name, expected: root.userData.shadowTriangles, triangles, chunks, bytes, outside });
      });
      return { error: window.__qaError, models: window.__game.tiles.models.size, visible: window.__game.tiles.tiles.group.children.length, rows };
    });
    if (audit.error || !audit.visible || !audit.rows.some(row => row.model === id) || audit.rows.some(row => row.triangles !== row.expected || row.outside)) throw new Error(`Invalid real-tile shadow audit: ${JSON.stringify(audit)}`);
    for (const top of [true, false]) {
      const pose = { E: 23459500 + x - (top ? 0 : 210), N: 6704500 - z - (top ? 0 : 210), H: top ? 450 : 240,
        yaw: top ? 0 : 45, pitch: top ? -90 : -38, fov: 50 };
      await page.evaluate(pose => window.__qaMove(pose), pose);
      await page.waitForFunction(() => window.__qaReady || window.__qaError, null, { timeout: 180000 });
      await page.screenshot({ path: path.join(output, `${id}-${top ? 'top' : 'oblique'}.png`) });
    }
    results.push({ id, url: url.href, ...audit });
    console.log(id, JSON.stringify({ models: audit.models, visible: audit.visible, chunks: audit.rows.reduce((sum, row) => sum + row.chunks, 0) }));
  }
  if (errors.length) throw new Error(errors.join('\n'));
} finally { fs.writeFileSync(path.join(output, 'audit.json'), JSON.stringify({ results, errors }, null, 2)); await browser.close(); }
