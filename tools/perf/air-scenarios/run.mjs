import { chromium } from '../../../game/node_modules/playwright-core/index.mjs';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { installScenario } from './runtime.mjs';
const [cdp, url, output] = process.argv.slice(2);
if (!output) throw new Error('Usage: node tools/perf/air-scenarios/run.mjs CDP URL OUTPUT');
fs.mkdirSync(output, { recursive: true });
const browser = await chromium.connectOverCDP(cdp);
const page = browser.contexts()[0].pages()[0];
const session = await page.context().newCDPSession(page);
await session.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 913, deviceScaleFactor: 2, mobile: false });
const recorderPath = process.env.TURKU3D_RENDER_RECORDER ?? path.join(process.env.HOME, 'turku3d/profiling/record.mjs');
const errors = []; page.on('pageerror', error => errors.push(error.message));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let recorder;
function record(stage, mode) {
  const dir = path.join(output, stage); fs.mkdirSync(dir, { recursive: true }); fs.rmSync(path.join(dir, 'STOP'), { force: true });
  recorder = spawn(process.execPath, [recorderPath, cdp, 'current', '180', dir, ...(mode ? [mode] : [])], { stdio: 'inherit' });
  return { stop: async () => { fs.writeFileSync(path.join(dir, 'STOP'), ''); await new Promise(resolve => recorder.on('exit', resolve)); recorder = null; } };
}
try {
  await session.send('Network.clearBrowserCache');
  await page.goto(url);
  await page.waitForFunction(() => window.__game?.ready || window.__qaError, null, { timeout: 300000 });
  const error = await page.evaluate(() => window.__qaError); if (error) throw new Error(error);
  await page.getByRole('option', { name: 'New game', exact: true }).click();
  await page.getByRole('button', { name: 'Skip', exact: true }).click();
  await pause(3000);
  const flight = record('flight', 'trace'); await pause(1500);
  const installed = await page.evaluate(installScenario, { flightSeconds: 75 });
  console.log('FLIGHT STARTED', JSON.stringify(installed));
  await page.waitForFunction(() => window.__air.phase === 'prepare-drive', null, { timeout: 180000 });
  await page.screenshot({ path: path.join(output, 'flight-end.png') });
  fs.writeFileSync(path.join(output, 'flight-state.json'), JSON.stringify(await page.evaluate(() => ({ frames: window.__air.frames, poses: window.__air.poses, points: window.__air.points, builders: window.__air.builders }))));
  await flight.stop();
  console.log('FLIGHT RECORDED');
  await page.evaluate(() => window.__air.prepareDrive());
  await page.waitForFunction(() => window.__game.state().mode === 'car', null, { timeout: 45000 });
  const car = record('drive'); await pause(1500);
  const route = await page.evaluate(() => window.__air.beginDrive());
  console.log('DRIVE STARTED', JSON.stringify({ route: route.route }));
  await pause(120000);
  await page.evaluate(() => window.__air.stop());
  await page.screenshot({ path: path.join(output, 'drive-end.png') });
  await car.stop();
  const result = await page.evaluate(() => ({ frames: window.__air.frames, poses: window.__air.poses, builders: window.__air.builders, route: window.__air.route,
    points: window.__air.points, driveStart: window.__air.driveStart, driveEnd: window.__air.driveEnd,
    perf: window.__perf, final: window.__game.state(), agent: navigator.userAgent }));
  fs.writeFileSync(path.join(output, 'scenario.json'), JSON.stringify({ url, installed, errors, ...result }));
  console.log('SCENARIO COMPLETE', JSON.stringify({ perf: result.perf, errors }));
} finally {
  try { fs.writeFileSync(path.join(output, 'last-state.json'), JSON.stringify(await page.evaluate(() => ({ air: window.__air, state: window.__game?.state?.(), error: window.__qaError })))); } catch {}
  recorder?.kill('SIGTERM'); await session.detach(); await browser.close(); }
