import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '../../../game/node_modules/playwright-core/index.mjs';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot, port } from '../../dev/environment.mjs';
import { installWarmFlight } from './warm-runtime.mjs';

isolateBrowser();
const [label, direction = 'north', url = 'https://wnt.github.io/turku-game/game/?dev=1'] = process.argv.slice(2);
if (!/^[a-z0-9-]+$/.test(label ?? '')) throw new Error('Usage: warm.mjs LABEL [north|east|south|west] [URL]');
const directions = { north: [0, -1], east: [1, 0], south: [0, 1], west: [-1, 0], northeast: [Math.SQRT1_2, -Math.SQRT1_2] };
if (!directions[direction]) throw new Error('Unknown direction');
const output = path.join(reportsRoot, 'warm-flight', label);
const profile = path.join(process.env.TURKU3D_BROWSER_DIR, label);
if (fs.existsSync(output)) throw new Error('Use a new evidence label');
fs.mkdirSync(output, { recursive: true });
const log = (file, row) => fs.appendFileSync(path.join(output, file), JSON.stringify({ at: Date.now(), ...row }) + '\n');
const context = await chromium.launchPersistentContext(profile, {
  executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs,
  headless: false, viewport: null, ignoreHTTPSErrors: true,
  args: chromeArgs(['--window-size=1280,900', '--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist',
    `--remote-debugging-port=${port(Number(process.env.FLIGHT_PORT_OFFSET ?? 3))}`]),
});
const deadline = setTimeout(() => void context.close(), 240000);
const page = context.pages()[0];
try {
  if (process.env.FLIGHT_BUILD) {
    const root = path.resolve(process.env.FLIGHT_BUILD);
    await page.route('https://wnt.github.io/turku-game/game/**', async route => {
      const relative = new URL(route.request().url()).pathname.slice('/turku-game/game/'.length) || 'index.html';
      const file = path.resolve(root, relative);
      if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) return route.continue();
      await route.fulfill({ path: file, headers: { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' } });
    });
  }
  if (process.env.FLIGHT_UPSTREAM) {
    await page.route('https://turku.lab.madekivi.fi/coarse/**', async route => {
      const url = new URL(route.request().url());
      const response = await route.fetch({ url: process.env.FLIGHT_UPSTREAM + url.pathname + url.search });
      await route.fulfill({ response }).catch(error => { if (!page.isClosed()) throw error; });
    });
  }
  await context.addInitScript(() => { Object.defineProperty(navigator, 'getGamepads', { value: () => [] }); });
  const session = await context.newCDPSession(page);
  await session.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await session.send('Network.enable');
  const requests = new Map();
  session.on('Network.requestWillBeSent', e => {
    requests.set(e.requestId, { at: e.timestamp, url: e.request.url });
    log('network.jsonl', { kind: 'request', id: e.requestId, url: e.request.url });
  });
  session.on('Network.responseReceived', e => {
    const r = requests.get(e.requestId);
    log('network.jsonl', { kind: 'response', id: e.requestId, url: e.response.url, status: e.response.status,
      ms: r ? (e.timestamp - r.at) * 1000 : null, timing: e.response.timing,
      sw: e.response.fromServiceWorker, disk: e.response.fromDiskCache, protocol: e.response.protocol });
  });
  session.on('Network.loadingFinished', e => {
    const r = requests.get(e.requestId);
    log('network.jsonl', { kind: 'finished', id: e.requestId, url: r?.url,
      ms: r ? (e.timestamp - r.at) * 1000 : null, bytes: e.encodedDataLength });
  });
  session.on('Network.loadingFailed', e => log('network.jsonl', { kind: 'failed', id: e.requestId, error: e.errorText }));
  page.on('console', message => log('console.jsonl', { kind: message.type(), text: message.text() }));
  page.on('pageerror', error => log('console.jsonl', { kind: 'exception', text: error.message }));
  console.log(JSON.stringify({ stage: 'navigation', output, url }));
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForFunction(() => window.__game?.ready || window.__qaError, null, { timeout: 120000 });
  const error = await page.evaluate(() => window.__qaError); if (error) throw new Error(error);
  fs.writeFileSync(path.join(output, 'menu.txt'), await page.locator('body').innerText());
  console.log(JSON.stringify({ stage: 'menu', text: (await page.locator('body').innerText()).slice(0, 1800) }));
  const multiplayer = page.locator('[data-option="multiplayer"]');
  if (await multiplayer.isVisible()) await multiplayer.click();
  else {
    await page.locator('[data-option="new"]').click();
    const skip = page.getByRole('button', { name: 'Skip', exact: true });
    if (await skip.isVisible()) await skip.click();
  }
  await page.waitForFunction(() => !window.__game.state().startScreen && !window.__game.state().loading, null, { timeout: 60000 });
  await page.waitForTimeout(5000);
  const [dx, dz] = directions[direction];
  // Full move + sprint uses FlyMode's real maximum; this value only scales the initial climb.
  console.log(JSON.stringify({ stage: 'flight', ...await page.evaluate(installWarmFlight, { dx, dz, metres: 2000, speed: 288 }) }));
  let shot = 0;
  while (!await page.evaluate(() => window.__warmFlight.done)) {
    const screenshot = await session.send('Page.captureScreenshot', { format: 'jpeg', quality: 65, fromSurface: true });
    fs.writeFileSync(path.join(output, `screen-${String(shot++).padStart(3, '0')}.jpg`), Buffer.from(screenshot.data, 'base64'));
    await page.waitForTimeout(1000);
    log('progress.jsonl', await page.evaluate(() => ({ phase: window.__warmFlight.phase, sample: window.__warmFlight.samples.at(-1) })));
  }
  const result = await page.evaluate(() => window.__warmFlight);
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result));
  console.log(JSON.stringify({ stage: 'complete', ...result.summary, output }));
  await page.waitForFunction(() => {
    const g = window.__game, [x, , z] = g.state().feet;
    const entry = g.coarse.entries.get(`t0_${Math.floor(x / 250)}_${Math.floor(z / 250)}`);
    return entry?.model && !entry.preview && g.streamer.supports(x, z);
  }, null, { timeout: 30000 });
  fs.writeFileSync(path.join(output, 'detail-ready.json'), JSON.stringify(await page.evaluate(() => window.__game.state())));
  const finalShot = await session.send('Page.captureScreenshot', { format: 'jpeg', quality: 80, fromSurface: true });
  fs.writeFileSync(path.join(output, 'detail-ready.jpg'), Buffer.from(finalShot.data, 'base64'));
  if (result.summary.missingVisualMs || result.summary.missingCollisionMs) throw new Error('Terrain or collision was missing during maximum-speed flight');
} catch (error) {
  try {
    fs.writeFileSync(path.join(output, 'partial.json'), JSON.stringify(await page.evaluate(() => ({ result: window.__warmFlight, state: window.__game?.state() }))));
    fs.writeFileSync(path.join(output, 'failure.txt'), await page.locator('body').innerText());
  } catch { /* The hard deadline may already have closed the page. */ }
  throw error;
} finally { clearTimeout(deadline); await context.close(); }
