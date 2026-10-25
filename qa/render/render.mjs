#!/usr/bin/env node
// Render the game from the poses in a view registry (qa/views/views.jsonl) via the QA URL contract
// (qa/README.md). For each view it writes, into --out:
//   <id>.png            colour pass at the reference's exact width x height
//   <id>.id.png         object-ID pass (?pass=id), unless --pass color
//   <id>.id.json        window.__qaIdTable (id -> {kind, id, tile, material})
//   <id>.meta.json      URL, pose echo, renderer info, console errors, timings
// and <out>/render-summary.json. Exit code 1 if any view failed.
//
// Usage (from qa/render, after `npm install`):
//   node render.mjs --views ../views/views.jsonl --url http://localhost:$TURKU3D_PORT_BASE/ --out ../../data/qa/runs/R/renders
//   node render.mjs --views ... --serve stub --out /tmp/x          # test against the contract stub
// Options: --ids a,b  --pass color|id|both (default both)  --backend webgpu|webgl  --timeout 120000
//          --channel chrome|chromium (default chromium)  --headed  --extra "k=v&k2=v2"
import { chromium } from 'playwright';
import { port, isolateBrowser } from '../../tools/dev/environment.mjs';
isolateBrowser();
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');

export function parseArgs(argv) {
  const a = { port: port(2), pass: 'both', timeout: 120000, channel: 'chromium', backend: null, headed: false, extra: '' };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    const next = () => argv[++i];
    if (k === '--port') a.port = Number(next());
    else if (k === '--views') a.views = next();
    else if (k === '--url') a.url = next();
    else if (k === '--serve') a.serve = next();
    else if (k === '--out') a.out = next();
    else if (k === '--ids') a.ids = next().split(',');
    else if (k === '--pass') a.pass = next();
    else if (k === '--backend') a.backend = next();
    else if (k === '--timeout') a.timeout = Number(next());
    else if (k === '--channel') a.channel = next();
    else if (k === '--extra') a.extra = next();
    else if (k === '--headed') a.headed = true;
    else throw new Error(`unknown option ${k}`);
  }
  if (!a.views || !a.out || !(a.url || a.serve)) throw new Error('need --views, --out and --url or --serve');
  if (!['color', 'id', 'both'].includes(a.pass)) throw new Error('invalid pass');
  return a;
}

const fmt = (x, d) => Number(x).toFixed(d).replace(/\.?0+$/, '') || '0';

// The URL contract. Keep in sync with qa/README.md.
export function contractUrl(base, view, { pass = 'color', backend = null, extra = '' } = {}) {
  const p = view.pose;
  const u = new URL(base);
  u.searchParams.set('cam', [fmt(p.E, 3), fmt(p.N, 3), fmt(p.H, 3), fmt(p.yaw, 3), fmt(p.pitch, 3), fmt(p.fov, 3)].join(','));
  u.searchParams.delete('roll');
  if (p.roll) u.searchParams.set('roll', fmt(p.roll, 3));
  u.searchParams.delete('time');
  if (view.time) u.searchParams.set('time', view.time); // URLSearchParams encodes '+' as %2B
  u.searchParams.set('qa', '1');
  u.searchParams.delete('pass');
  if (pass === 'id') u.searchParams.set('pass', 'id');
  if (backend) u.searchParams.set('backend', backend);
  for (const [k, v] of new URLSearchParams(extra)) u.searchParams.set(k, v);
  return u.toString();
}

function serveDir(dir, port) {
  const types = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg' };
  const srv = http.createServer((req, res) => {
    const rel = decodeURIComponent(new URL(req.url, 'http://x').pathname).replace(/^\/+/, '') || 'index.html';
    const f = path.resolve(dir, rel);
    if (!f.startsWith(path.resolve(dir)) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'content-type': types[path.extname(f)] || 'application/octet-stream' });
    fs.createReadStream(f).pipe(res);
  });
  return new Promise((ok, reject) => { srv.once('error', reject); srv.listen(port, '127.0.0.1', () => ok(srv)); });
}

export function loadViews(file, ids) {
  const rows = fs.readFileSync(file, 'utf8').split('\n').filter((l) => l.trim() && !l.trim().startsWith('#')).map((l) => JSON.parse(l));
  return ids ? rows.filter((v) => ids.includes(v.id)) : rows;
}

async function shoot(browser, url, view, file, timeout) {
  const ctx = await browser.newContext({ viewport: { width: view.width, height: view.height }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const consoleLines = [];
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') consoleLines.push(`[${m.type()}] ${m.text()}`); });
  page.on('pageerror', (e) => consoleLines.push(`[pageerror] ${e.message}`));
  const t0 = Date.now();
  let error = null, state = {};
  try {
    await page.goto(url, { waitUntil: 'load', timeout });
    await page.waitForFunction(() => window.__qaReady === true || typeof window.__qaError === 'string', null, { timeout, polling: 100 });
    state = await page.evaluate(() => ({ ready: window.__qaReady === true, error: window.__qaError ?? null, pose: window.__qaPose ?? null,
      info: window.__qaInfo ?? null, idTable: window.__qaIdTable ?? null, inner: [innerWidth, innerHeight] }));
    if (state.error) error = `page reported __qaError: ${state.error}`;
    else await page.screenshot({ path: file, type: 'png', animations: 'disabled', caret: 'hide' });
  } catch (e) {
    error = String(e.message || e).split('\n')[0];
  }
  await ctx.close();
  return { ...state, url, file: error ? null : file, ms: Date.now() - t0, error, console: consoleLines };
}

export function poseEcho(view, echoed) {
  if (!echoed) return 'missing window.__qaPose';
  for (const k of ['E', 'N', 'H', 'yaw', 'pitch', 'roll', 'fov']) if (!Number.isFinite(echoed[k]) || Math.abs(echoed[k] - (view.pose[k] ?? 0)) > 1e-3) return `pose echo mismatch on ${k}: ${echoed[k]} vs ${view.pose[k]}`;
  return null;
}

export async function main(argv = process.argv.slice(2), sharedBrowser = null) {
  const a = parseArgs(argv);
  const views = loadViews(path.resolve(a.views), a.ids);
  if (!views.length || (a.ids && a.ids.some(id => !views.some(v => v.id === id)))) throw new Error('empty or unknown view selection');
  fs.mkdirSync(a.out, { recursive: true });
  let srv = null, base = a.url;
  if (a.serve) {
    const dir = a.serve === 'stub' ? path.join(HERE, 'stub') : path.resolve(a.serve);
    srv = await serveDir(dir, a.port);
    base = `http://127.0.0.1:${srv.address().port}/index.html`;
  }
  const browser = sharedBrowser ?? await chromium.launch({
    channel: a.channel === 'chrome' ? 'chrome' : undefined, headless: !a.headed,
    args: ['--enable-unsafe-webgpu', '--enable-features=Vulkan,WebGPU', '--use-angle=metal', '--ignore-gpu-blocklist', '--hide-scrollbars'],
  });
  const summary = [];
  let failed = 0;
  for (const v of views) {
    const passes = a.pass === 'both' ? ['color', 'id'] : [a.pass];
    const meta = { view_id: v.id, width: v.width, height: v.height, pose: v.pose, time: v.time ?? null, passes: {} };
    for (const pass of passes) {
      const file = path.join(a.out, pass === 'id' ? `${v.id}.id.png` : `${v.id}.png`);
      fs.rmSync(file, { force: true });
      if (pass === 'id') fs.rmSync(path.join(a.out, `${v.id}.id.json`), { force: true });
      const r = await shoot(browser, contractUrl(base, v, { pass, backend: a.backend, extra: a.extra }), v, file, a.timeout);
      if (!r.error) r.error = poseEcho(v, r.pose);
      if (!r.error && (r.inner[0] !== v.width || r.inner[1] !== v.height)) r.error = `viewport ${r.inner} != ${v.width}x${v.height}`;
      if (pass === 'id' && !r.error) {
        if (!r.idTable) r.error = 'missing window.__qaIdTable';
        else fs.writeFileSync(path.join(a.out, `${v.id}.id.json`), JSON.stringify(r.idTable, null, 1));
      }
      delete r.idTable;
      meta.passes[pass] = r;
      if (r.error) { failed++; fs.rmSync(file, { force: true }); }
      console.log(`${v.id} [${pass}] ${r.error ? 'FAIL ' + r.error : 'ok ' + r.ms + ' ms'} `);
    }
    fs.writeFileSync(path.join(a.out, `${v.id}.meta.json`), JSON.stringify(meta, null, 1));
    summary.push(meta);
  }
  fs.writeFileSync(path.join(a.out, 'render-summary.json'), JSON.stringify({ base, backend: a.backend, browser: browser.version(), views: summary }, null, 1));
  if (!sharedBrowser) await browser.close();
  if (srv) srv.close();
  return failed;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().then((failed) => process.exit(failed ? 1 : 0), (e) => { console.error(e); process.exit(2); });
}
export { REPO };
