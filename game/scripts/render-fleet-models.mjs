// Render converted fleet models through the workshop (runtime visuals path) into PNGs.
//   BROWSER_CDP=... node scripts/render-fleet-models.mjs <base-url> <out-dir> <id[,id...]>... [--views a,b] [--lods 0,1,2] [--color silver]
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
const args = process.argv.slice(2), opt = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const [base, out, ...rest] = args.filter((a, i) => !a.startsWith('--') && !args[i - 1]?.startsWith('--'));
const views = opt('--views', 'three-quarter,side,front,rear').split(','), lods = opt('--lods', '0').split(',').map(Number), color = opt('--color', 'silver');
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.connectOverCDP(process.env.BROWSER_CDP);
const context = browser.contexts()[0] ?? await browser.newContext();
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
await page.setViewportSize({ width: 1280, height: 720 });
page.on('console', m => { if (m.type() === 'error') console.error('page:', m.text()); });
await page.goto(`${base}/fleet-gallery.html`);
await page.waitForFunction(() => window.fleetWorkshop, null, { timeout: 120000 });
for (const group of rest) for (const lod of lods) for (const view of views) {
  const ids = group.split(',');
  const snap = await page.evaluate(([ids, lod, color, view]) => window.fleetWorkshop.authored(ids, lod, color, view), [ids, lod, color, view]);
  await page.waitForTimeout(400);
  const file = path.join(out, `${ids.length > 1 ? 'row' : ids[0]}-lod${lod}-${view}.png`);
  await page.screenshot({ path: file });
  console.log(file, JSON.stringify(snap).slice(0, 160));
}
await page.close();
await browser.close();
