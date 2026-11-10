// Lists the failed requests of a game load and start: BROWSER_CDP=... BASE=... node find-404.mjs
import { BASE, connect, openGame, sleep } from './lib.mjs';
const browser = await connect();
const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
const page = await context.newPage();
const bad = [];
page.on('response', r => { if (r.status() >= 400) bad.push(`${r.status()} ${r.url().slice(0, 160)}`); });
page.on('requestfailed', r => bad.push(`failed ${r.failure()?.errorText} ${r.url().slice(0, 160)}`));
await page.goto(`${BASE}?quality=low&telemetry=0&traffic=0&walkers=0&room=sf-404-${Date.now() % 10000}`, { waitUntil: 'domcontentloaded', timeout: 180000 });
await page.waitForFunction(() => window.__game?.ready, null, { timeout: 400000 });
await page.evaluate(() => document.querySelector('[data-option="multiplayer"]')?.dispatchEvent(new PointerEvent('pointerup', { bubbles: true })));
await page.waitForFunction(() => { const s = window.__game.state(); return !s.loading && !s.startScreen; }, null, { timeout: 400000 });
await sleep(8000);
console.log(JSON.stringify([...new Set(bad)], null, 1));
await context.close(); process.exit(0);
