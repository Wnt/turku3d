// Headless Chrome: splat.html (Spark + mesh + Rapier). Arg "webgpu" tries WebGPURenderer.
import { spawn } from 'node:child_process';
import { chromium } from 'playwright';
const webgpu = process.argv.includes('webgpu');
const srv = spawn('./node_modules/.bin/vite', ['preview', '--port', '4174', '--strictPort'], { stdio: 'pipe' });
await new Promise((res) => srv.stdout.on('data', (d) => /Local/.test(String(d)) && res()));
const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
const lines = [];
page.on('console', (m) => lines.push(`[${m.type()}] ${m.text().slice(0, 300)}`));
page.on('pageerror', (e) => lines.push(`[pageerror] ${e.message}`));
await page.goto(`http://localhost:4174/splat.html${webgpu ? '?webgpu=1' : ''}`);
await page.waitForFunction(() => window.__probe?.done, null, { timeout: 90000 }).catch(() => lines.push('TIMEOUT'));
const p = await page.evaluate(() => ({ ...window.__probe, verdict: window.__probe.verdict && { stairs: window.__probe.verdict.stairsClimbed, car: window.__probe.verdict.carMountedCurbAndRamp } }));
await page.screenshot({ path: `splat-${webgpu ? 'webgpu' : 'webgl'}.png` });
console.log(JSON.stringify(p, null, 1));
console.log(lines.slice(0, 15).join('\n'));
await browser.close(); srv.kill();
