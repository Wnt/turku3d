import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';
import { isolateBrowser, reportsRoot, workRoot, port } from '../../tools/dev/environment.mjs';

isolateBrowser();
const label = process.argv[2] ?? 'after', seconds = Number(process.argv[3] ?? 36);
const out = path.join(reportsRoot, 'material-stream'); fs.mkdirSync(out, { recursive: true });
const manifestPath = path.join(out, 'assets.json');
let urls;
if (fs.existsSync(manifestPath)) urls = JSON.parse(fs.readFileSync(manifestPath));
else {
  urls = await Promise.all(['t0_0_0', 't0_0_-1', 't0_1_0'].map(async id => {
    const root = `https://turku.lab.madekivi.fi/coarse/${id}/`;
    const response = await fetch(`${root}collision.json`, { signal: AbortSignal.timeout(10000) });
    assert(response.ok); const manifest = await response.json();
    return new URL(manifest.visual[id].url, root).href;
  }));
  fs.writeFileSync(manifestPath, JSON.stringify(urls));
}
const server = await createServer({ configFile: false, root: fileURLToPath(new URL('../', import.meta.url)),
  publicDir: 'public', cacheDir: path.join(workRoot, 'cache/material-vite'),
  plugins: [{ name: 'baseline-source', enforce: 'pre', load(id) {
    const relative = path.relative(fileURLToPath(new URL('../../', import.meta.url)), id.split('?')[0]);
    if (process.env.MATERIAL_BASELINE && relative.startsWith('game/src/')) return execFileSync('git', ['show', `${process.env.MATERIAL_BASELINE}:${relative}`], { encoding: 'utf8' });
  } }, { name: 'material-probe', configureServer(server) { server.middlewares.use((req, res, next) => {
    if (req.url === '/probe') { res.setHeader('Content-Type', 'text/html'); res.end('<script type="module" src="/scripts/material-stream-scene.mjs"></script>'); } else next();
  }); } }], server: { host: '127.0.0.1', port: port(3), strictPort: true,
    headers: { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' } } });
await server.listen();
let browser;
try {
  browser = await chromium.launch({ executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs,
    headless: true, args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']) });
  const page = await browser.newPage();
  await page.exposeFunction('captureMaterialStream', () => page.screenshot({ path: path.join(out, `${label}.png`) }));
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') console.log(message.text()); });
  await page.goto(`http://127.0.0.1:${port(3)}/probe`);
  await page.waitForFunction(() => window.runMaterialStream);
  if (label === 'prepare') {
    const result = await page.evaluate(() => window.runMaterialPreparation());
    fs.writeFileSync(path.join(out, 'prepare.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
    assert.ok(result.preBuilders > 0); assert.ok(result.snapshots.every(Boolean));
    assert.equal(result.lateBuilders, 0); assert.deepEqual(result.errors, []); assert.deepEqual(errors, []);
  } else {
  console.log(JSON.stringify({ stage: 'loaded', label, urls }));
  const result = await page.evaluate(([urls, seconds, tier, moving]) => window.runMaterialStream(urls, seconds, tier, moving), [urls, seconds, process.env.PROBE_QUALITY ?? 'low', process.env.PROBE_MOVING === '1']);
  result.pageErrors = errors;
  const percentile = (values, p) => [...values].sort((a, b) => a - b)[Math.ceil(values.length * p) - 1];
  result.summary = result.rows.map(({ frames, costs, drawFrames, ...row }) => ({ ...row,
    drawCallsMean: drawFrames.reduce((sum, f) => sum + f.drawCalls, 0) / drawFrames.length,
    passMean: Object.fromEntries(['pre', 'beauty', 'reflection', 'shadow', 'quad'].map(p => [p, drawFrames.reduce((sum, f) => sum + (f.passes[p] ?? 0), 0) / drawFrames.length])),
    reflectionFrames: drawFrames.filter(f => f.passes.reflection).length,
    transparentMean: Object.fromEntries(['beauty', 'reflection'].map(p => [p, drawFrames.reduce((sum, f) => sum + (f.transparentLists[p] ?? 0), 0) / drawFrames.length])),
    frameP50: percentile(frames, .5), frameP90: percentile(frames, .9), cpuP50: percentile(costs, .5) }));
  fs.writeFileSync(path.join(out, `${label}.json`), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ report: out, seconds: result.seconds, rows: result.summary, errors, gpuErrors: result.errors }));
  assert.deepEqual(errors, []); assert.deepEqual(result.errors, []);
  const rounds = result.rows.filter(row => [2, 5, 8].includes(row.step));
  assert.equal(rounds.length, 3);
  assert.equal(rounds[2].materials, rounds[1].materials, 'same resident tiles have stable materials');
  assert.equal(rounds[2].renderObjects, rounds[1].renderObjects, 'same resident tiles have stable render objects');
  assert.equal(rounds[2].memory.attributes, rounds[1].memory.attributes, 'same resident tiles have stable GPU attributes');
  assert.equal(rounds[2].memory.attributesSize, rounds[1].memory.attributesSize, 'GPU attribute bytes plateau');
  assert.ok(rounds[2].pipelines <= rounds[1].pipelines + 2, 'pipeline cache plateaus');
  }
} finally { await browser?.close(); await server.close(); }
