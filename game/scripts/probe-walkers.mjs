// Walker probe (src/world/walkers): starts a solo game in headless Chrome for Testing, visits central street spots
// and writes screenshots plus walker/perf snapshots (drawn, within 50 m, update/scan/build ms) to <out-dir>.
// Needs a local game with draft tiles (tools/dev/coarse-tiles or tools/dev/localhost-game) and solo play
// (unset VITE_PUBLIC_RELEASE for the dev server). Plans: tour (default), closeup (SPOT=river, WAIT=ms),
// ab (exact walker draws per pass), toggle (frame timings with the walker group on/off).
// Usage: TURKU3D_PORT_BASE=... node scripts/probe-walkers.mjs "http://127.0.0.1:$TURKU3D_PORT_BASE/?hud=0" <out-dir> [plan]
import { chromium } from '@playwright/test';
import fs from 'node:fs';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser } from '../../tools/dev/environment.mjs';

isolateBrowser();
const [url, outDir, plan = 'tour'] = process.argv.slice(2);
fs.mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch({ ignoreDefaultArgs: chromeIgnoreDefaultArgs, executablePath: chromeExecutable(), headless: !process.env.HEADED,
  args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const logs = [];
page.on('console', m => { if (m.type() !== 'debug') logs.push(`[${m.type()}] ${m.text()}`); });
page.on('pageerror', e => logs.push(`[pageerror] ${e.message}`));
const done = async () => { fs.writeFileSync(`${outDir}/console.log`, logs.join('\n')); await browser.close(); };
await page.goto(url);
try {
  await page.getByRole('option', { name: 'New game' }).click({ timeout: 120_000 });
  await page.getByRole('button', { name: 'Skip' }).click({ timeout: 60_000 }).catch(() => {});
  await page.waitForFunction(() => window.__game?.ready === true && window.__game.state().grounded && !window.__game.state().paused, null, { timeout: 180_000 });
} catch (e) { await page.screenshot({ path: `${outDir}/fail.png` }); await done(); throw e; }
await page.keyboard.press('v'); // hide the player character

const snap = () => page.evaluate(() => {
  const g = window.__game, f = g.state().feet, near = [];
  for (const m of g.walkers.meshes?.meshes ?? []) for (let i = 0; i < m.count; i++) {
    const e = m.instanceMatrix.array; near.push(Math.hypot(e[16 * i + 12] - f[0], e[16 * i + 14] - f[2]));
  }
  return { walkers: g.walkers.snapshot(), within50: near.filter(d => d <= 50).length, frameMs: g.state().frameMs, feet: f,
    cpu: window.__perf?.cpuMs, gpu: window.__perf?.gpuMs, errors: g.errors.slice(-3) };
});
const teleport = (x, z) => page.evaluate(async ([x, z]) => {
  const g = window.__game, hit = await g.physics.raycast({ x, y: 300, z }, { x: 0, y: -1, z: 0 }, 600, 'foot');
  g.physics.setCharacterEnabled(true, { x, y: (hit?.point.y ?? 5) + 0.2, z });
}, [x, z]);
/** Turn the orbit camera towards the nearest drawn walker (or their centroid) from the player. */
const face = (distance = 6, pitch = -0.12, nearest = false) => page.evaluate(([distance, pitch, nearest]) => {
  const g = window.__game, f = g.state().feet; let sx = 0, sz = 0, n = 0, best = null;
  for (const m of g.walkers.meshes.meshes) for (let i = 0; i < m.count; i++) {
    const e = m.instanceMatrix.array, x = e[16 * i + 12], z = e[16 * i + 14], d = Math.hypot(x - f[0], z - f[2]);
    if (d > 60) continue; sx += x; sz += z; n++; if (!best || d < best.d) best = { x, z, d };
  }
  if (!n) return null;
  const tx = nearest ? best.x : sx / n, tz = nearest ? best.z : sz / n;
  const o = g.orbit; o.yaw = Math.atan2(-(tx - f[0]), -(tz - f[2])); o.pitch = pitch; o.distance = distance;
  return { n, nearest: best.d };
}, [distance, pitch, nearest]);

const spots = { office: [168, 246], aurakatu: [195, -40], eerikinkatu: [350, -150], kauppiaskatu: [320, 5], river: [230, 300] };
const results = {};
if (plan === 'tour') {
  for (const [name, [x, z]] of Object.entries(spots)) {
    await teleport(x, z);
    await page.waitForTimeout(4000);
    const aimed = await face(7, -0.15);
    await page.waitForTimeout(600);
    await page.screenshot({ path: `${outDir}/${name}.png` });
    results[name] = { aimed, ...(await snap()) };
  }
}
if (plan === 'closeup') {
  const [x, z] = spots[process.env.SPOT ?? 'aurakatu'];
  await teleport(x, z);
  await page.waitForTimeout(Number(process.env.WAIT ?? 4000));
  for (let k = 0; k < 10; k++) {
    results[`close-${k}`] = await face(3.2, -0.05, true);
    await page.waitForTimeout(120);
    await page.screenshot({ path: `${outDir}/close-${k}.png` });
  }
}
if (plan === 'ab') {
  const [x, z] = spots[process.env.SPOT ?? 'river'];
  await teleport(x, z);
  await page.waitForTimeout(8000);
  await page.evaluate(() => { const o = window.__game.orbit; o.yaw = 1.2; o.pitch = -0.15; o.distance = 7; });
  await page.waitForTimeout(2000);
  results.draws = await page.evaluate(() => new Promise(resolve => {
    const r = window.__render.renderer, original = r.renderObject;
    const counts = { walker: 0, walkerPrepass: 0, all: 0, frames: 0, instances: 0 };
    r.renderObject = function (object, scene, camera, geometry, material, ...rest) {
      counts.all++;
      if (object.name?.startsWith('walkers-')) {
        counts.walker++; counts.instances += object.count;
        if (scene.overrideMaterial?.userData?.normalPrepass) counts.walkerPrepass++;
      }
      return original.call(this, object, scene, camera, geometry, material, ...rest);
    };
    let n = 0;
    const tick = () => { if (++n < 120) requestAnimationFrame(tick); else { r.renderObject = original; counts.frames = n; resolve(counts); } };
    requestAnimationFrame(tick);
  }));
  await page.evaluate(() => window.__render.perf.reset());
  await page.waitForTimeout(6000);
  results.perf = await page.evaluate(() => window.__perf);
  await page.screenshot({ path: `${outDir}/ab.png` });
}
if (plan === 'toggle') {
  const [x, z] = spots[process.env.SPOT ?? 'river'];
  await teleport(x, z);
  await page.waitForTimeout(8000);
  results.aim = await face(8, -0.1);
  await page.waitForTimeout(1000);
  await page.screenshot({ path: `${outDir}/toggle.png` });
  results.windows = [];
  for (let k = 0; k < 6; k++) {
    const on = k % 2 === 0;
    await page.evaluate(v => { window.__game.walkers.group.visible = v; window.__render.perf.reset(); }, on);
    await page.waitForTimeout(5000);
    results.windows.push(await page.evaluate(v => ({ on: v, drawn: window.__game.walkers.count, cpu: window.__perf.cpuMs.p50, gpu: window.__perf.gpuMs.p50,
      gpu95: window.__perf.gpuMs.p95, res: window.__perf.resolution, update: window.__game.walkers.updateMs }), on));
  }
}
results.end = await snap();
fs.writeFileSync(`${outDir}/results.json`, JSON.stringify(results, null, 2));
await done();
