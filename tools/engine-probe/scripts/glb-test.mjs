import { spawn } from 'node:child_process';
import { chromium } from 'playwright';
import { port } from './environment.mjs';
const PORT = port(6);
const srv = spawn('./node_modules/.bin/vite', ['preview', '--port', String(PORT), '--strictPort'], { stdio: 'pipe' });
await new Promise((res) => srv.stdout.on('data', (d) => /Local/.test(String(d)) && res()));
const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--enable-unsafe-webgpu', '--use-angle=metal'] });
for (const q of ['f=level.etc1s.meshopt.glb', 'f=level.uastc.meshopt.glb', 'f=level.etc1s.meshopt.glb&webgl']) {
  const page = await browser.newPage();
  const lines = []; page.on('pageerror', (e) => lines.push(e.message)); page.on('console', (m) => m.type() === 'error' && lines.push(m.text()));
  await page.goto(`http://localhost:${PORT}/glb.html?${q}`);
  await page.waitForFunction(() => window.__probe?.done, null, { timeout: 120000 });
  console.log(q, JSON.stringify(await page.evaluate(() => window.__probe)), lines.join(' | '));
  await page.close();
}
await browser.close(); srv.kill();
