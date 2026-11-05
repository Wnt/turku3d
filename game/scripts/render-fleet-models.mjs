// Capture vehicle-gallery.html sections (the real runtime draw and physics) into PNGs over CDP.
//   BROWSER_CDP=... node scripts/render-fleet-models.mjs <base-url> <out-dir> [id ...]   (no ids: every section)
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
const [base, out, ...ids] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.connectOverCDP(process.env.BROWSER_CDP);
const context = browser.contexts()[0] ?? await browser.newContext();
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
await page.setViewportSize({ width: 1280, height: 900 });
await page.goto(`${base}/vehicle-gallery.html`);
await page.waitForSelector('.view[data-id]', { timeout: 120000 });
const all = await page.$$eval('.view[data-id]', views => views.map(v => v.dataset.id));
for (const id of ids.length ? ids : all) {
  const view = page.locator(`.view[data-id="${id}"]`);
  await view.scrollIntoViewIfNeeded();
  // The section in view takes the live renderer, settles its car on the step block and opens its chase view.
  await page.waitForFunction(sel => document.querySelector(sel)?.classList.contains('active'), `.view[data-id="${id}"]`, { timeout: 60000 });
  await page.waitForTimeout(1500);
  await view.screenshot({ path: path.join(out, `${id}.png`) });
  console.log(id);
}
console.log('errors', JSON.stringify(errors.slice(0, 5)));
await page.close(); await browser.close();
