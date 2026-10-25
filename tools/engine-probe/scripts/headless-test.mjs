// Headless Chrome test: serves dist/ via `vite preview`, opens ?auto=1, collects
// console + verdict. Usage: node scripts/headless-test.mjs [chrome|chromium] [webgl]
import { spawn } from 'node:child_process';
import { chromium } from 'playwright';

const channel = process.argv[2] === 'chromium' ? undefined : 'chrome';
const forceWebGL = process.argv.includes('webgl');
const headed = process.argv.includes('headed');
const PORT = 4173;

const srv = spawn('./node_modules/.bin/vite', ['preview', '--port', String(PORT), '--strictPort'], { stdio: 'pipe' });
await new Promise((res, rej) => {
  srv.stdout.on('data', (d) => /Local/.test(String(d)) && res());
  srv.on('exit', (c) => rej(new Error('preview exited ' + c)));
  setTimeout(() => rej(new Error('preview timeout')), 15000);
});

const browser = await chromium.launch({
  channel,
  headless: !headed,
  args: ['--enable-unsafe-webgpu', '--enable-features=Vulkan,WebGPU', '--use-angle=metal', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
const consoleLines = [];
page.on('console', (m) => consoleLines.push(`[${m.type()}] ${m.text()}`));
page.on('pageerror', (e) => consoleLines.push(`[pageerror] ${e.message}`));

const url = `http://localhost:${PORT}/?auto=1${forceWebGL ? '&webgl=1' : ''}`;
const t0 = Date.now();
await page.goto(url);
await page.waitForFunction(() => window.__probe && window.__probe.done, null, { timeout: 60000 });
const probe = await page.evaluate(() => {
  const p = window.__probe;
  return { adapter: p.adapter, glRenderer: p.glRenderer, backend: p.backend, three: p.three, rapier: p.rapier, verdict: p.verdict, errors: p.errors,
    physMsPerFrame: p.physMsPerFrame, renderMsPerFrame: p.renderMsPerFrame,
    gpu: navigator.gpu ? 'navigator.gpu present' : 'no navigator.gpu',
    sab: typeof SharedArrayBuffer !== 'undefined', coi: self.crossOriginIsolated,
    samples: p.samples.filter((_, i) => i % 2 === 0).map((s) => ({ t: s.t, stairs: s.walkers.stairs, r15: s.walkers.ramp15, r55: s.walkers.ramp55, car: { x: s.car.x, y: s.car.y, v: s.car.speed, w: s.car.wheelsInContact } })) };
});
await page.screenshot({ path: `probe-${channel ?? 'chromium'}${forceWebGL ? '-webgl' : ''}.png` });
console.log(`loaded+ran in ${Date.now() - t0} ms, browser ${browser.version()}`);
console.log(JSON.stringify({ ...probe, samples: undefined }, null, 1));
for (const s of probe.samples)
  console.log(`t=${s.t.toFixed(2).padStart(5)} stairs(x=${s.stairs.x.toFixed(2)},y=${s.stairs.feetY.toFixed(3)},g=${+s.stairs.grounded}) r15(x=${s.r15.x.toFixed(2)},y=${s.r15.feetY.toFixed(3)}) r55(x=${s.r55.x.toFixed(2)},y=${s.r55.feetY.toFixed(3)}) car(x=${s.car.x.toFixed(2)},y=${s.car.y.toFixed(3)},v=${s.car.v},w=${s.car.w})`);
console.log('--- console ---\n' + consoleLines.join('\n'));
await browser.close();
srv.kill();
const v = probe.verdict;
const ok = v.stairsClimbed && v.ramp15Climbed && v.ramp55Blocked && v.carMountedCurbAndRamp && probe.errors.length === 0 && !consoleLines.some((l) => l.startsWith('[pageerror]'));
console.log(ok ? 'PASS' : 'FAIL');
process.exit(ok ? 0 : 1);
