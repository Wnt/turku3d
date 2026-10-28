// Assisted physical drive using real hosted draft meshes, through this sandbox's localhost game.
// Guidance targets are explicit; this diagnostic does not claim a clean player delivery.
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot, port } from '../../tools/dev/environment.mjs';

isolateBrowser();
const [metadataFile, destination = 'forum-marinum', seconds = '180'] = process.argv.slice(2);
const metadata = JSON.parse(fs.readFileSync(metadataFile));
const address = metadata.addresses.addresses.find(a => a.id === destination);
if (!address) throw Error(`missing destination ${destination}`);
const out = path.join(reportsRoot, 'route-drive', destination);
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ executablePath: chromeExecutable(), headless: true,
  ignoreDefaultArgs: chromeIgnoreDefaultArgs, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
const samples = [], errors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1100, height: 700 } });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await page.route('**/coarse/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/coarse/navigation.json') return route.fulfill({ json: metadata });
    if (['/coarse/status', '/coarse/want'].includes(url.pathname)) return route.fulfill({ json: { state: 'online' } });
    try {
      const response = await route.fetch({ url: `https://turku.lab.madekivi.fi${url.pathname}${url.search}`, timeout: 15000 });
      await route.fulfill({ response });
    } catch {
      await route.fulfill({ status: 503, json: { state: 'offline' } }).catch(() => {});
    }
  });
  page.on('pageerror', e => { errors.push(e.message); console.log('PAGEERROR', e.message); });
  console.log('opening localhost', destination);
  await page.goto(`http://localhost:${process.env.ROUTE_DRIVE_PORT ?? port()}/?backend=webgpu&quality=low`);
  await page.waitForFunction(() => window.__game?.ready || window.__qaError, null, { timeout: 180000 }).catch(async error => {
    console.log('STARTUP', await page.locator('body').innerText());
    await page.screenshot({ path: path.join(out, 'startup.png') });
    fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify({ destination, outcome: 'startup failed', errors, message: error.message }));
    throw error;
  });
  console.log('ready', destination);
  const failure = await page.evaluate(() => window.__qaError);
  if (failure) throw Error(failure);
  await page.getByRole('option', { name: 'New game', exact: true }).click();
  await page.getByRole('button', { name: 'Skip', exact: true }).click();
  await page.waitForFunction(() => !window.__game.state().loading && !window.__game.state().startScreen, null, { timeout: 120000 });
  await page.evaluate(async ({ metadata, address }) => {
    const { parseRoadGraph } = await import('/src/gameplay/roadGraph.ts');
    const g = window.__game, graph = parseRoadGraph(metadata.roads);
    g.router.setGraph(graph); g.minimap.setGraph(graph); g.coarse.onMapChange = null;
    const target = { id: address.id, label: address.address, kind: 'dropoff', point: { x: address.point[0], z: address.point[1] } };
    g.minimap.setSource({ subscribe(fn) { fn({ targets: [target], activeTargetId: target.id, message: address.address, money: 0, timerSeconds: null }); return () => {}; } });
    const v = g.state().vehicle;
    g.router.mode = 'car';
    const path = g.router.route({ x: v.pos[0], z: v.pos[2] }, target.point);
    if (!path || path.length < 2) throw Error('no initial driving route');
    const start = path[0], next = path[1], yaw = Math.atan2(-(next.z - start.z), next.x - start.x);
    const h = await g.physics.raycast({ x: start.x, y: 500, z: start.z }, { x: 0, y: -1, z: 0 }, 1000);
    if (!h) throw Error('street start has no ground');
    // Put the diagnostic car on its initial street, then drive continuously from there.
    g.physics.resetVehicle({ x: start.x, y: h.point.y + .8, z: start.z }, yaw);
    g.physics.setCharacterEnabled(true, { x: start.x + Math.sin(yaw) * 2, y: h.point.y + .1, z: start.z + Math.cos(yaw) * 2 });
  }, { metadata, address });
  await page.waitForTimeout(1000);
  await page.keyboard.press('KeyF');
  await page.waitForFunction(() => window.__game.state().mode === 'car', null, { timeout: 10000 });
  await page.evaluate(() => {
    const g = window.__game, original = g.input.update.bind(g.input);
    let lastHint = null;
    g.input.update = function(dt) {
      original(dt);
      const s = g.state(), r = g.minimap.routes.route;
      if (s.mode !== 'car' || !r?.length) return;
      if (r !== lastHint) { g.coarse.setRouteHint(r); lastHint = r; }
      let goal = r.at(-1), look = 8 + Math.abs(s.vehicle.forwardSpeed) * .5;
      for (let i = 1; i < r.length; i++) {
        const a = r[i - 1], b = r[i], len = Math.hypot(b.x - a.x, b.z - a.z);
        if (look <= len) { goal = { x: a.x + (b.x - a.x) * look / len, z: a.z + (b.z - a.z) * look / len }; break; }
        look -= len;
      }
      const q = s.vehicle.rot, yaw = Math.atan2(2 * (q.w * q.y + q.x * q.z), 1 - 2 * (q.y * q.y + q.z * q.z));
      const desired = Math.atan2(-(goal.z - s.feet[2]), goal.x - s.feet[0]);
      const error = Math.atan2(Math.sin(desired - yaw), Math.cos(desired - yaw));
      const speed = Math.abs(s.vehicle.forwardSpeed), target = Math.abs(error) > .5 ? 3 : Math.abs(error) > .2 ? 6 : 12;
      this.actions.steer = Math.max(-1, Math.min(1, -error * 3));
      this.actions.throttle = speed < target ? .8 : 0;
      this.actions.brake = speed > target + 1 ? .4 : 0;
    };
  });
  let outcome = 'time limit';
  for (let elapsed = 0; elapsed < Number(seconds); elapsed += 5) {
    await page.waitForTimeout(5000);
    const sample = await page.evaluate(() => {
      const g = window.__game, s = g.state();
      return { feet: s.feet, speed: s.vehicle.forwardSpeed, remaining: g.minimap.routes.length,
        route: !!g.minimap.routes.route, recovery: s.recovery, draft: g.coarse.status };
    });
    samples.push({ elapsed: elapsed + 5, ...sample });
    console.log(JSON.stringify(samples.at(-1)));
    if (!sample.route) { outcome = 'guidance missing'; break; }
    if (sample.remaining < 12) { outcome = 'route end reached'; break; }
    if (samples.length > 6 && Math.hypot(sample.feet[0] - samples.at(-6).feet[0], sample.feet[2] - samples.at(-6).feet[2]) < 2) { outcome = 'physical blocker (30s stationary)'; break; }
  }
  await page.screenshot({ path: path.join(out, 'drive.png') });
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify({ destination, outcome, samples, errors }, null, 2));
  console.log(JSON.stringify({ destination, outcome, samples: samples.length, errors }));
} finally { await browser.close(); }
