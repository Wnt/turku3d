import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { chromium } from '../../../game/node_modules/playwright-core/index.mjs';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot, port } from '../../dev/environment.mjs';
import { installCarDrive } from './car-runtime.mjs';

isolateBrowser();
const label = process.argv[2];
if (!/^[a-z0-9-]+$/.test(label ?? '')) throw new Error('Supply a unique evidence label');
const expectedRelease = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
const contexts = [], drivers = [
  { name: 'west', target: { x: -1200, z: 900 } },
  { name: 'east', target: { x: 1200, z: -450 } },
];
const deadline = setTimeout(() => contexts.forEach(context => void context.close()), 360000);
try {
  const sessions = await Promise.all(drivers.map(async (driver, index) => {
    const output = path.join(reportsRoot, 'car-streaming', `${label}-${driver.name}`);
    if (fs.existsSync(output)) throw new Error('Evidence label already exists');
    fs.mkdirSync(output, { recursive: true });
    const context = await chromium.launchPersistentContext(path.join(process.env.TURKU3D_BROWSER_DIR, `${label}-${driver.name}`), {
      executablePath: chromeExecutable(), headless: false, viewport: null, ignoreDefaultArgs: chromeIgnoreDefaultArgs,
      args: chromeArgs(['--window-size=1280,900', '--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist',
        `--remote-debugging-port=${port(3 + index)}`]),
    });
    contexts.push(context);
    await context.addInitScript(() => { Object.defineProperty(navigator, 'getGamepads', { value: () => [] }); });
    const page = context.pages()[0], cdp = await context.newCDPSession(page);
    await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
    page.on('pageerror', error => fs.appendFileSync(path.join(output, 'errors.txt'), error.message + '\n'));
    const publishedDeadline = Date.now() + 120000;
    for (;;) {
      await page.goto(`https://wnt.github.io/turku-game/game/?dev=1&car_probe=${label}-${driver.name}&publication=${Date.now()}`, { waitUntil: 'domcontentloaded' });
      const published = await page.locator('meta[name="turku3d-release"]').getAttribute('content', { timeout: 1000 }).catch(() => null);
      if (published === expectedRelease) break;
      if (Date.now() >= publishedDeadline) throw new Error(`Pages serves ${published}, expected ${expectedRelease}`);
      await page.waitForTimeout(3000);
    }
    await page.waitForFunction(() => window.__game?.ready, null, { timeout: 120000 });
    await page.locator('[data-option="multiplayer"]').click();
    await page.waitForFunction(() => !window.__game.state().startScreen && !window.__game.state().loading);
    await page.waitForTimeout(5000);
    const placement = await page.evaluate(async target => {
      const g = window.__game, car = g.state().vehicles.find(vehicle => vehicle.id === 'player');
      if (!car) throw new Error('Starter car missing');
      g.router.mode = 'car';
      const route = g.router.route({ x: car.pos[0], z: car.pos[2] }, target);
      if (!route || route.length < 2) throw new Error('No street route');
      let left = 25, p, next;
      for (let i = 1; i < route.length; i++) {
        const a = route[i - 1], b = route[i], distance = Math.hypot(b.x - a.x, b.z - a.z);
        if (left <= distance) { p = { x: a.x + (b.x - a.x) * left / distance, z: a.z + (b.z - a.z) * left / distance }; next = b; break; }
        left -= distance;
      }
      if (!p) throw new Error('Route too short');
      const hit = await g.physics.raycast({ x: p.x, y: 500, z: p.z }, { x: 0, y: -1, z: 0 }, 1000);
      if (!hit) throw new Error('Initial street has no collision');
      const yaw = Math.atan2(-(next.z - p.z), next.x - p.x);
      g.physics.resetVehicle({ x: p.x, y: hit.point.y + .8, z: p.z }, yaw);
      g.physics.setCharacterEnabled(true, { x: p.x + Math.sin(yaw) * 2, y: hit.point.y + .1, z: p.z + Math.cos(yaw) * 2 });
      return { point: p, height: hit.point.y, route, target };
    }, driver.target);
    fs.writeFileSync(path.join(output, 'placement.json'), JSON.stringify(placement));
    await page.waitForTimeout(700);
    await page.keyboard.press('KeyF');
    await page.waitForFunction(() => window.__game.state().mode === 'car', null, { timeout: 10000 });
    console.log(JSON.stringify({ stage: 'car-ready', driver: driver.name, output }));
    return { ...driver, page, cdp, output };
  }));
  const results = await Promise.all(sessions.map(async session => {
    const route = await session.page.evaluate(installCarDrive, { target: session.target, seconds: 75 });
    console.log(JSON.stringify({ stage: 'driving', driver: session.name, points: route.route.length }));
    let shot = 0;
    while (!await session.page.evaluate(() => window.__carDrive.done)) {
      const screenshot = await session.cdp.send('Page.captureScreenshot', { format: 'jpeg', quality: 65, fromSurface: true });
      fs.writeFileSync(path.join(session.output, `screen-${String(shot++).padStart(3, '0')}.jpg`), Buffer.from(screenshot.data, 'base64'));
      await session.page.waitForTimeout(5000);
      const sample = await session.page.evaluate(() => window.__carDrive.samples.at(-1));
      fs.appendFileSync(path.join(session.output, 'progress.jsonl'), JSON.stringify(sample) + '\n');
      console.log(JSON.stringify({ driver: session.name, ...sample }));
    }
    const result = await session.page.evaluate(() => window.__carDrive);
    fs.writeFileSync(path.join(session.output, 'result.json'), JSON.stringify({ release: expectedRelease, ...result }));
    console.log(JSON.stringify({ stage: 'complete', driver: session.name, ...result.summary }));
    return result;
  }));
  const overlapMs = Math.min(...results.map(r => r.startedAt + r.elapsedMs)) - Math.max(...results.map(r => r.startedAt));
  if (overlapMs < 60000) throw new Error(`Insufficient simultaneous driving: ${overlapMs} ms`);
  for (const [index, result] of results.entries()) {
    const s = result.summary;
    if (s.travelled < 350 || s.crossings < 3 || s.peakKmh < 45) throw new Error(`Insufficient route coverage for ${drivers[index].name}: ${JSON.stringify(s)}`);
    if (s.missingDetailMs || s.missingVisualMs || s.missingCollisionMs || s.resets) throw new Error(`Streaming failed for ${drivers[index].name}: ${JSON.stringify(s)}`);
    if (!result.samples.every(sample => sample.multiplayer >= 2)) throw new Error('The two multiplayer sessions were not connected throughout');
  }
  console.log(JSON.stringify({ stage: 'passed', overlapMs, summaries: results.map(r => r.summary) }));
} finally { clearTimeout(deadline); await Promise.allSettled(contexts.map(context => context.close())); }
