#!/usr/bin/env node
// Measures what a player's frame costs in a location of the world pipeline, on any origin (the dev server or the
// published lab): frame rate, CPU and GPU time, GPU passes, draw calls and triangles, standing at the start and
// looking four ways, then driving. Run it in a headed Chrome for Testing under an exclusive lease:
//
//   python3 ~/turku3d/tools/host-lease/session.py run mac NAME --kind perf --headed --window-size 1728,1000 --load-timeout 400 -- \
//     node scripts/dev/world-perf.mjs --url 'https://city.lab.madekivi.fi/?world=portsa&time=14:00' --out DIR [--seconds 5] [--census] [--profile]
//
// The camera is held at the player's eyes (window.__game.holdCamera) for the four looks, so two runs of one location
// measure the same views. `--census` adds one frame's draws by pass and group (window.__render.census) for the
// first look, `--profile` the main thread's CPU profile there (self time per function; meaningful on the dev
// server's unminified code). report.json holds every number; the summary line is printed.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { chromium } = createRequire(path.join(repo, 'game/package.json'))('playwright');
const argv = process.argv.slice(2);
const opt = (name, fallback) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : fallback; };
const url = opt('--url'), out = opt('--out'), seconds = Number(opt('--seconds', 5)), timeout = Number(opt('--ready-timeout', 300)) * 1000;
if (!url || !out || !process.env.BROWSER_CDP) { console.error('usage: BROWSER_CDP=... world-perf.mjs --url URL --out DIR [--seconds 5] [--census]'); process.exit(2); }
fs.mkdirSync(out, { recursive: true });

const report = { url, started: new Date().toISOString(), looks: [], notes: [], console: [] };
const browser = await chromium.connectOverCDP(process.env.BROWSER_CDP);
const context = browser.contexts()[0] ?? await browser.newContext();
const page = await context.newPage();
page.on('console', m => { if (m.type() === 'error') report.console.push(m.text().slice(0, 300)); });
page.on('pageerror', e => report.console.push(`pageerror: ${String(e).slice(0, 300)}`));
const still = async name => { await page.screenshot({ path: path.join(out, `${name}.png`) }); };
const finish = async code => {
  report.ended = new Date().toISOString();
  fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 1) + '\n');
  await page.close().catch(() => {});
  await browser.close().catch(() => {});
  process.exit(code);
};
/** Resets the monitor, lets `ms` of frames pass and returns what it measured. */
const measure = ms => page.evaluate(async ms => {
  const { perf, renderer } = window.__render;
  perf.reset();
  await new Promise(done => setTimeout(done, ms));
  await perf.flush?.().catch(() => {});
  const { cpuMs, gpuMs, frameMs, drawCalls, triangles, resolution, windowFrames, gpuStatus } = window.__perf;
  const passes = perf.passSnapshot();
  return { fps: frameMs.p50 ? Math.round(10000 / frameMs.p50) / 10 : null, frameMs, cpuMs, gpuMs, drawCalls, triangles, resolution, frames: windowFrames, gpuStatus,
    scale: window.__game.resolution?.scale ?? null, governor: window.__game.governor?.level ?? null,
    passes: Object.fromEntries(Object.entries(passes.gpuMs).map(([name, p]) => [name, p.p50]).filter(([, v]) => v)),
    memory: renderer.info.memory };
}, ms);

/** The main thread's CPU profile over `ms`: self time per function, the heaviest first (use an unminified build). */
async function profile(ms) {
  const session = await context.newCDPSession(page);
  await session.send('Profiler.enable');
  await session.send('Profiler.setSamplingInterval', { interval: 200 });
  await session.send('Profiler.start');
  await page.waitForTimeout(ms);
  const { profile } = await session.send('Profiler.stop');
  await session.detach();
  const self = new Map(), byId = new Map(profile.nodes.map(node => [node.id, node]));
  profile.samples.forEach((id, i) => {
    const frame = byId.get(id).callFrame, key = `${frame.functionName || '(anonymous)'} ${frame.url.replace(/^.*\/(src|node_modules)\//, '$1/').replace(/\?.*$/, '')}:${frame.lineNumber}`;
    self.set(key, (self.get(key) ?? 0) + profile.timeDeltas[i] / 1000);
  });
  const total = (profile.endTime - profile.startTime) / 1000;
  return { ms: total, top: [...self].sort((a, b) => b[1] - a[1]).slice(0, 60).map(([name, t]) => [name, Math.round(t), `${(100 * t / total).toFixed(1)} %`]) };
}

const t0 = Date.now();
try {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForFunction(() => window.__game?.state?.().mode && window.__game.state().mode !== 'loading' || !!document.querySelector('.fatal, #fatal'),
    null, { timeout, polling: 500 });
  const playing = () => page.evaluate(() => { const s = window.__game.state(); return !s.startScreen && !s.loading; });
  for (const began = Date.now(); !await playing();) {
    if (Date.now() - began > 150000) throw new Error('the session did not start within 150 s');
    await page.waitForTimeout(500);
  }
  report.loadSeconds = (Date.now() - t0) / 1000;
  report.client = await page.evaluate(() => ({ quality: window.__game.params.quality, backend: window.__game.backend, dpr: devicePixelRatio,
    window: [innerWidth, innerHeight], census: window.__dev?.world?.census?.() ?? null }));
  await page.waitForTimeout(8000);
  const feet = await page.evaluate(() => window.__game.state().feet);
  for (let k = 0; k < 4; k++) {
    const yaw = k * Math.PI / 2;
    await page.evaluate(([feet, yaw]) => window.__game.holdCamera({ position: [feet[0], feet[1] + 1.7, feet[2]],
      quaternion: [0, Math.sin(yaw / 2), 0, Math.cos(yaw / 2)], fov: window.__game.state().camera.fov }), [feet, yaw]);
    await page.waitForTimeout(1500);
    const look = { look: k * 90, ...await measure(seconds * 1000) };
    report.looks.push(look);
    await still(`look-${k * 90}`);
    if (k === 0 && argv.includes('--census')) report.census = await page.evaluate(() => window.__render.census?.()).catch(error => `census failed: ${error}`);
    if (k === 0 && argv.includes('--profile')) report.profile = await profile(3000);
    console.error(`[perf] look ${look.look}: ${look.fps} fps, cpu ${look.cpuMs.p50?.toFixed(1)} ms, gpu ${look.gpuMs.p50?.toFixed(1) ?? '-'} ms, ${look.drawCalls} draws, ${look.triangles} triangles, scale ${look.scale}`);
  }
  await page.evaluate(() => window.__game.holdCamera(null));
  // Driving: the chase camera, the car and whatever streams in on the way.
  await page.evaluate(() => window.__game.enterVehicle('player'));
  await page.waitForTimeout(2500);
  await page.keyboard.down('KeyW');
  report.drive = await measure(seconds * 1000);
  await page.keyboard.up('KeyW');
  await still('drive');
  console.error(`[perf] drive: ${report.drive.fps} fps, cpu ${report.drive.cpuMs.p50?.toFixed(1)} ms, gpu ${report.drive.gpuMs.p50?.toFixed(1) ?? '-'} ms, ${report.drive.triangles} triangles`);
} catch (error) {
  report.notes.push(`failed: ${String(error).slice(0, 400)}`);
  await still('failure').catch(() => {});
  await finish(1);
}
const fps = report.looks.map(l => l.fps);
console.log(JSON.stringify({ url, loadSeconds: report.loadSeconds, client: { ...report.client, census: undefined }, fpsLooks: fps, fpsDrive: report.drive?.fps,
  triangles: report.looks.map(l => l.triangles), draws: report.looks.map(l => l.drawCalls), scale: report.looks.map(l => l.scale),
  cpuMs: report.looks.map(l => l.cpuMs.p50), gpuMs: report.looks.map(l => l.gpuMs.p50), passes: report.looks[0]?.passes }));
await finish(0);
