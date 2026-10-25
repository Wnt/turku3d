// Headless Chrome (real Metal GPU, vsync off) frame-time measurement of fx.html variants.
import { spawn } from 'node:child_process';
import { chromium } from 'playwright';
const srv = spawn('./node_modules/.bin/vite', ['preview', '--port', '4175', '--strictPort'], { stdio: 'pipe' });
await new Promise((res) => srv.stdout.on('data', (d) => /Local/.test(String(d)) && res()));
const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist', '--disable-gpu-vsync', '--disable-frame-rate-limit'] });
for (const q of (process.argv[2] ?? 'fx=0;fx=1;fx=1&webgl').split(';')) {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const lines = [];
  page.on('console', (m) => m.type() !== 'log' && lines.push(`[${m.type()}] ${m.text().slice(0, 200)}`));
  page.on('pageerror', (e) => lines.push(`[pageerror] ${e.message}`));
  await page.goto(`http://localhost:4175/fx.html?${q}`);
  await page.waitForFunction(() => window.__probe?.done, null, { timeout: 120000 }).catch(() => lines.push('TIMEOUT'));
  const p = await page.evaluate(() => window.__probe);
  await page.screenshot({ path: `fx-${q.replace(/[^a-z0-9]/gi, '_')}.png` });
  console.log(q, JSON.stringify(p), lines.filter((l) => !/404/.test(l)).slice(0, 5).join(' | '));
  await page.close();
}
await browser.close(); srv.kill();
