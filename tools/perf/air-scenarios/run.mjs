import { chromium } from '../../../game/node_modules/playwright-core/index.mjs';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { installScenario } from './runtime.mjs';
const [cdp, url, output, selection = 'both', driveSeconds = '120'] = process.argv.slice(2);
if (!['both', 'flight', 'drive'].includes(selection)) throw new Error('Scenario must be both, flight or drive');
if (!output) throw new Error('Usage: node tools/perf/air-scenarios/run.mjs CDP URL OUTPUT');
fs.mkdirSync(output, { recursive: true });
const browser = await chromium.connectOverCDP(cdp);
const page = browser.contexts()[0].pages()[0];
const session = await page.context().newCDPSession(page);
await session.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 913, deviceScaleFactor: 2, mobile: false });
const recorderPath = process.env.TURKU3D_RENDER_RECORDER ?? path.join(process.env.HOME, 'turku3d/profiling/record.mjs');
const errors = []; page.on('pageerror', error => errors.push(error.message));
page.on('console', message => { if (message.type() === 'error') { errors.push(message.text()); console.error('PAGE ERROR', message.text()); } });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let recorder;
function record(stage, mode) {
  const dir = path.join(output, stage); fs.mkdirSync(dir, { recursive: true }); fs.rmSync(path.join(dir, 'STOP'), { force: true });
  recorder = spawn(process.execPath, [recorderPath, cdp, 'current', '180', dir, ...(mode ? [mode] : [])], { stdio: 'inherit' });
  return { stop: async () => { fs.writeFileSync(path.join(dir, 'STOP'), ''); await new Promise(resolve => recorder.on('exit', resolve)); recorder = null; } };
}
try {
  // Unload first: the game's pagehide autosave must finish before clearing the target origin.
  await page.goto('about:blank');
  await session.send('Storage.clearDataForOrigin', { origin: new URL(url).origin, storageTypes: 'all' });
  await page.evaluate(() => { try { localStorage.clear(); sessionStorage.clear(); } catch {} });
  await session.send('Network.clearBrowserCache');
  await page.goto(url);
  await page.waitForFunction(() => window.__game?.ready || window.__qaError, null, { timeout: 300000 });
  const error = await page.evaluate(() => window.__qaError); if (error) throw new Error(error);
  const join = page.getByRole('option', { name: /^Multiplayer/ });
  if (await join.isVisible()) await join.click();
  else await page.getByRole('option', { name: 'New game', exact: true }).click();
  // Leaving the preceding game can autosave after storage is cleared. Handle the real confirmation
  // and intro flow, and require the start screen to close before injecting driving input.
  const menuDeadline = Date.now() + 60000;
  while (Date.now() < menuDeadline) {
    const confirm = page.getByRole('option', { name: 'Start a new game', exact: true });
    if (await confirm.isVisible()) await confirm.click();
    const skip = page.getByRole('button', { name: 'Skip', exact: true });
    if (await skip.isVisible()) await skip.click();
    if (await page.evaluate(() => !window.__game.state().paused && !window.__game.state().startScreen)) break;
    await pause(100);
  }
  await page.waitForFunction(() => !window.__game.state().paused && !window.__game.state().startScreen, null, { timeout: 1000 });
  await pause(3000);
  const flight = selection !== 'drive' ? record('flight', process.env.TURKU3D_RENDER_TRACE === '0' ? null : 'trace') : null;
  if (flight) await pause(1500);
  const installed = await page.evaluate(installScenario, { flightSeconds: flight ? 75 : 0 });
  if (flight) {
    console.log('FLIGHT STARTED', JSON.stringify(installed));
    await page.waitForFunction(() => window.__air.phase === 'prepare-drive', null, { timeout: 180000 });
    await page.screenshot({ path: path.join(output, 'flight-end.png') });
    fs.writeFileSync(path.join(output, 'flight-state.json'), JSON.stringify(await page.evaluate(() => ({ work: window.__air.work, frames: window.__air.frames, poses: window.__air.poses, points: window.__air.points, builders: window.__air.builders, physics: window.__air.physics }))));
    await flight.stop(); console.log('FLIGHT RECORDED');
  }
  if (selection !== 'flight') {
    await page.evaluate(() => window.__air.prepareDrive());
    await page.waitForFunction(() => window.__game.state().mode === 'car', null, { timeout: 45000 });
    const car = record('drive', process.env.TURKU3D_DRIVE_TRACE === '1' ? 'trace' : null); await pause(1500);
    const route = await page.evaluate(() => window.__air.beginDrive());
    console.log('DRIVE STARTED', JSON.stringify({ route: route.route }));
    await pause(Number(driveSeconds) * 1000);
    await page.evaluate(() => window.__air.stop());
    fs.writeFileSync(path.join(output, 'drive-state.json'), JSON.stringify(await page.evaluate(() => ({ work: window.__air.work,
      frames: window.__air.frames, poses: window.__air.poses, builders: window.__air.builders, physics: window.__air.physics,
      route: window.__air.route, extensions: window.__air.extensions, traceAnchor: window.__air.traceAnchor }))));
    await page.screenshot({ path: path.join(output, 'drive-end.png') });
    await page.evaluate(() => window.__render.renderer.setAnimationLoop(null));
    await car.stop();
  } else await page.evaluate(() => window.__air.stop());
  const result = await page.evaluate(() => ({ work: window.__air.work, frames: window.__air.frames, poses: window.__air.poses, builders: window.__air.builders, physics: window.__air.physics, extensions: window.__air.extensions, route: window.__air.route,
    points: window.__air.points, driveStart: window.__air.driveStart, driveEnd: window.__air.driveEnd,
    perf: window.__perf, final: window.__game.state(), agent: navigator.userAgent }));
  fs.writeFileSync(path.join(output, 'scenario.json'), JSON.stringify({ url, selection, driveSeconds: Number(driveSeconds), installed, errors, ...result }));
  console.log('SCENARIO COMPLETE', JSON.stringify({ perf: result.perf, errors }));
} finally {
  try { fs.writeFileSync(path.join(output, 'last-state.json'), JSON.stringify(await page.evaluate(() => ({ air: window.__air, state: window.__game?.state?.(), error: window.__qaError })))); } catch {}
  recorder?.kill('SIGTERM'); await session.detach(); await browser.close(); }
