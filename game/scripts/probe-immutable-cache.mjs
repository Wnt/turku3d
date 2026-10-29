// Verify real OCI bytes across a browser restart and a network outage.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const root = 'https://turku.lab.madekivi.fi/coarse/t0_0_0/';
const fragment = await (await fetch(`${root}collision.json`)).json();
const assets = [fragment.visual.t0_0_0, Object.values(fragment.collision)[0]]
  .map(entry => ({ url: new URL(entry.url, root).href, hash: entry.sha256, bytes: entry.bytes }));
const worker = process.env.IMMUTABLE_WORKER ? fs.readFileSync(process.env.IMMUTABLE_WORKER)
  : await (await fetch(`https://wnt.github.io/turku-game/game/isolation.js?cache-probe=${Date.now()}`)).text();
assert(String(worker).includes('turku3d-immutable-v1'), 'Published worker must contain the immutable cache');
// A real, inert Pages document remains navigable when the worker forwards the
// restarted browser's navigation directly to the network.
const url = 'https://wnt.github.io/turku-game/game/manifest.webmanifest';
const launch = async () => {
  const context = await chromium.launchPersistentContext(path.join(process.env.TURKU3D_BROWSER_DIR, 'immutable-probe'), {
    executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs, headless: true, args: chromeArgs(),
  });
  context.on('requestfailed', request => console.log(JSON.stringify({ failed: request.url(), error: request.failure()?.errorText })));
  context.on('console', message => { if (message.type() === 'error') console.log(message.text()); });
  await context.route('https://wnt.github.io/turku-game/game/**', route => route.fulfill({
    contentType: route.request().url().endsWith('/isolation.js') ? 'application/javascript' : 'text/html',
    headers: { 'Cache-Control': 'no-store' },
    body: route.request().url().endsWith('/isolation.js') ? worker : '<!doctype html><title>Immutable cache probe</title>',
  }));
  return context;
};
let context;
const result = {};
try {
  context = await launch();
  let page = await context.newPage();
  await page.goto(url);
  await page.evaluate(async () => {
    for (const registration of await navigator.serviceWorker.getRegistrations()) await registration.unregister();
    for (const key of await caches.keys()) if (key.startsWith('turku3d-immutable-')) await caches.delete(key);
  });
  await page.reload();
  const read = async () => page.evaluate(async assets => Promise.all(assets.map(async asset => {
    const start = performance.now(), response = await fetch(asset.url, { cache: 'reload' });
    const buffer = await response.arrayBuffer();
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', buffer))].map(v => v.toString(16).padStart(2, '0')).join('');
    return { ...asset, status: response.status, actual_bytes: buffer.byteLength, actual_hash: hash,
      cached: response.headers.has('X-Turku3D-Cache-Bytes'), milliseconds: performance.now() - start };
  })), assets);
  result.before = await read();
  await page.evaluate(async () => {
    await navigator.serviceWorker.register('./isolation.js', { updateViaCache: 'none' });
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller) await new Promise(resolve => navigator.serviceWorker.addEventListener('controllerchange', resolve, { once: true }));
  });
  result.cold = await read();
  await page.waitForFunction(async count => (await (await caches.open('turku3d-immutable-v1')).keys()).length === count, assets.length);
  await context.close(); context = await launch();
  page = await context.newPage();
  await page.goto(url);
  await context.setOffline(true);
  result.restarted_offline = await read();
  for (const row of [...result.before, ...result.cold, ...result.restarted_offline]) {
    assert.equal(row.status, 200); assert.equal(row.actual_hash, row.hash); assert.equal(row.actual_bytes, row.bytes);
  }
  assert(result.restarted_offline.every(row => row.cached));
  const output = path.join(reportsRoot, 'tile-arrival', 'immutable-cache.json');
  fs.mkdirSync(path.dirname(output), { recursive: true }); fs.writeFileSync(output, JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ report: output, ...result }));
} finally { await context?.close(); }
