// Real Metal GPU material compilation + optional before/after captures. Own server/profile only.
import { chromium } from '@playwright/test';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import { PNG } from 'pngjs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const work = process.env.TURKU3D_WORK;
const port = Number(process.env.GROUND_QA_PORT ?? process.env.TURKU3D_PORT_BASE ?? 5310);
if (!work || !process.env.TURKU3D_BROWSER_DIR) throw new Error('Source sandbox.env first');
const real = process.argv.includes('--real');
const output = path.join(work, 'reports/ground-qa', ...(real ? [] : ['synthetic']));
await fs.mkdir(output, { recursive: true });
const server = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--config', 'vite.ground.config.ts', '--port', String(port), '--strictPort'],
  { cwd: root, stdio: 'pipe', env: process.env });
let serverLog = '';
server.stdout.on('data', chunk => { serverLog += chunk; });
server.stderr.on('data', chunk => { serverLog += chunk; });
const base = `http://localhost:${port}`;
const results = [];
const started = performance.now();
try {
  let ready = false;
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(serverLog);
    try { if ((await fetch(`${base}/ground-qa.html`)).ok) { ready = true; break; } } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert(ready, `Vite did not start: ${serverLog}`);
  for (const backend of ['webgpu', 'webgl']) {
    const context = await chromium.launchPersistentContext(path.join(process.env.TURKU3D_BROWSER_DIR, `ground-${backend}`), {
      channel: 'chrome', headless: true, viewport: { width: 1280, height: 960 },
      args: ['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist'],
    });
    try {
      for (const view of real ? [0, 1, 2] : [0]) {
        for (const capture of real ? ['before', 'after'] : ['after']) {
          const page = await context.newPage();
          const errors = [];
          page.on('pageerror', err => errors.push(err.message));
          page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
          const query = new URLSearchParams({ backend, view: String(view), capture });
          if (real) query.set('assets', `/@fs/${work}/textures/`);
          await page.goto(`${base}/ground-qa.html?${query}`, { waitUntil: 'networkidle' });
          await page.waitForFunction(() => window.groundQA?.ready, { timeout: 120000 });
          const result = await page.evaluate(() => window.groundQA);
          if (!real) await fs.writeFile(path.join(output, `${backend}-shader.json`), JSON.stringify(await page.evaluate(() => window.groundShader)));
          assert.equal(errors.length, 0, errors.join('\n'));
          assert(result.calls > 0);
          const file = `${backend}-${view}-${capture}.png`;
          const screenshot = await page.screenshot({ path: path.join(output, file) });
          if (!real) {
            const pixels = PNG.sync.read(screenshot);
            const deviations = [300, 550, 750, 1050].map(x0 => {
              let sum = 0, squares = 0, count = 0;
              for (let y = 800; y < 900; y++) for (let x = x0; x < x0 + 50; x++) {
                const value = pixels.data[(y * pixels.width + x) * 4];
                sum += value; squares += value ** 2; count++;
              }
              return Math.sqrt(Math.max(0, squares / count - (sum / count) ** 2));
            });
            assert(deviations.every(d => d > .4), `Material channels lost detail: ${deviations}`);
            result.detailStandardDeviation = deviations;
          }
          assert.equal(result.backend, backend === 'webgpu' ? 'WebGPUBackend' : 'WebGLBackend');
          results.push({ ...result, file, sha256: createHash('sha256').update(screenshot).digest('hex'), errors });
          console.log(`PASS ${backend} view=${view} ${capture}`);
          await page.close();
        }
      }
    } finally { await context.close(); }
  }
  await fs.writeFile(path.join(output, real ? 'real.json' : 'synthetic.json'), JSON.stringify(results, null, 2));
  console.log(`Ground QA completed in ${((performance.now() - started) / 1000).toFixed(2)} s`);
} finally {
  server.kill('SIGTERM');
}
