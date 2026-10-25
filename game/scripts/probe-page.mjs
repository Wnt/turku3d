// Debug helper: opens a URL in headless Chrome (Metal GPU), prints console output + HUD text, saves a screenshot.
// Usage: node scripts/probe-page.mjs "http://localhost:5173/?backend=webgl" [out.png] [waitMs]
import { chromium } from 'playwright';
const [url, out = 'test-results/probe.png', wait = '6000'] = process.argv.slice(2);
const browser = await chromium.launch({ channel: 'chrome', headless: !process.env.HEADED, args: ['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
page.on('console', (m) => console.log(`[${m.type()}] ${m.text()}`));
page.on('pageerror', (e) => console.log(`[pageerror] ${e.message}`));
await page.goto(url);
await page.waitForTimeout(Number(wait));
console.log('HUD:', await page.evaluate(() => document.getElementById('hud')?.textContent));
console.log('STATE:', await page.evaluate(() => JSON.stringify(window.__game?.state?.() ?? null)));
await page.screenshot({ path: out });
await browser.close();
