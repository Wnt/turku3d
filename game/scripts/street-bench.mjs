#!/usr/bin/env node
// Street-camera bench driver (street-richness plan, Phase 0). For each camera x time x quality it opens
// ?bench=camera&id=<id>&at=<HH:MM>&quality=<tier> on a built game, waits for window.__bench and saves the JSON
// plus a screenshot.
//
// Tiles come from the published coarse service through a read-only local proxy. /status is answered locally,
// /want and /demand return 404, so the bench never steers the host's prebuild. Content-addressed files are
// cached on disk.
//
// Usage (port base = 10 private ports; never 5170/5171/5175):
//   TURKU3D_WORK=<scratch> node scripts/build.mjs
//   node scripts/street-bench.mjs --dist <scratch>/build/game --out <dir> --port-base 5480 [--headed]
//     [--cameras riverside,shopping] [--at 13:00,22:00] [--quality medium,high] [--repeat 1]
//     [--upstream https://turku.lab.madekivi.fi/coarse/] [--cache <dir>]
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';
import { chromeArgs, chromeIgnoreDefaultArgs, chromeExecutable } from '../../tools/dev/chrome-args.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../..');
const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const list = value => value.split(',').map(s => s.trim()).filter(Boolean);
const dist = path.resolve(opt('--dist', ''));
const out = path.resolve(opt('--out', ''));
const base = Number(opt('--port-base', '0'));
if (!opt('--dist') || !opt('--out')) throw new Error('--dist and --out are required');
if (!Number.isInteger(base) || base < 1024 || [5170, 5171, 5175].some(p => p >= base && p <= base + 9)) {
  throw new Error('--port-base must be a private range clear of 5170-5175');
}
const cameras = JSON.parse(fs.readFileSync(path.join(repo, 'game/src/qa/street-cameras.json'), 'utf8')).cameras;
const ids = list(opt('--cameras', cameras.map(c => c.id).join(',')));
const times = list(opt('--at', '13:00,22:00'));
const tiers = list(opt('--quality', 'medium,high'));
const repeat = Number(opt('--repeat', '1'));
const upstream = new URL(opt('--upstream', 'https://turku.lab.madekivi.fi/coarse/'));
const cache = path.resolve(opt('--cache', path.join(out, 'cache')));
const headed = args.includes('--headed');
fs.mkdirSync(cache, { recursive: true });
fs.mkdirSync(out, { recursive: true });

// ----------------------------------------------------------------------------------- read-only coarse proxy
const immutable = /(?:^|\.)[0-9a-f]{64}\.(?:glb|col|json|bin|ktx2|png|jpg)$/; // vis.<sha>.glb, shared/<sha>.bin
const types = { '.json': 'application/json', '.glb': 'model/gltf-binary', '.bin': 'application/octet-stream',
  '.col': 'application/octet-stream', '.ktx2': 'image/ktx2', '.png': 'image/png', '.jpg': 'image/jpeg' };
const traffic = { upstream: 0, cached: 0, bytes: 0, errors: 0 };
const proxy = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://local');
  const send = (status, body, type = 'application/json') => { res.writeHead(status, { 'Content-Type': type }); res.end(body); };
  if (req.method !== 'GET' && req.method !== 'HEAD') return send(405, '{"error":"read-only"}');
  const name = url.pathname.replace(/^\/coarse\//, '');
  if (name === 'status') return send(200, '{"state":"online","bench":"read-only proxy"}');
  if (name === 'want' || name === 'demand') return send(404, '{"error":"not proxied"}');
  const file = path.basename(url.pathname);
  const hit = immutable.test(file) ? path.join(cache, file) : null;
  if (hit && fs.existsSync(hit)) { traffic.cached++; return send(200, fs.readFileSync(hit), types[path.extname(file)] ?? 'application/octet-stream'); }
  try {
    const r = await fetch(new URL(name + url.search, upstream), { headers: { 'User-Agent': 'turku3d-street-bench' } });
    const body = Buffer.from(await r.arrayBuffer());
    traffic.upstream++; traffic.bytes += body.length;
    if (hit && r.status === 200) fs.writeFileSync(hit, body);
    send(r.status, body, r.headers.get('content-type') ?? 'application/octet-stream');
  } catch (error) {
    traffic.errors++;
    send(502, JSON.stringify({ error: String(error) }));
  }
});
await new Promise(resolve => proxy.listen(base + 5, '127.0.0.1', resolve));

// ----------------------------------------------------------------------------------- game server
const server = spawn(process.execPath, [path.join(repo, 'tools/dev/serve-dist.mjs'), '--root', dist, '--port', String(base + 1),
  '--coarse', `http://127.0.0.1:${base + 5}`, '--telemetry-dir', path.join(out, 'telemetry')],
{ stdio: ['ignore', 'inherit', 'inherit'], env: { ...process.env, TURKU3D_PORT_BASE: String(base) } });
const origin = `http://127.0.0.1:${base + 1}`;
for (let i = 0; i < 100; i++) {
  if (await fetch(`${origin}/`).then(r => r.ok, () => false)) break;
  await new Promise(r => setTimeout(r, 100));
}

// ----------------------------------------------------------------------------------- browser
const profile = path.join(out, 'chrome-profile');
const context = await chromium.launchPersistentContext(profile, {
  executablePath: chromeExecutable(), ignoreDefaultArgs: chromeIgnoreDefaultArgs, headless: !headed,
  viewport: { width: 1600, height: 913 }, deviceScaleFactor: 2,
  args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist', '--disable-gpu-vsync',
    '--disable-frame-rate-limit', '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
    '--disable-backgrounding-occluded-windows']),
});
const page = context.pages()[0] ?? await context.newPage();
const pageErrors = [];
page.on('pageerror', e => pageErrors.push(e.message));
page.on('console', m => { if (m.type() === 'error') pageErrors.push(m.text()); });
page.on('requestfailed', r => pageErrors.push(`request failed: ${r.url()} ${r.failure()?.errorText ?? ''}`));
page.on('response', r => { if (r.status() >= 400 && !/\/coarse\/(want|demand)/.test(r.url())) pageErrors.push(`HTTP ${r.status()} ${r.url()}`); });
const timeoutMs = Number(opt('--timeout', '300')) * 1000;

const results = [];
try {
  for (let r = 0; r < repeat; r++) {
    for (const id of ids) {
      for (const at of times) {
        for (const quality of tiers) {
          pageErrors.length = 0;
          const url = `${origin}/?bench=camera&id=${encodeURIComponent(id)}&at=${at}&quality=${quality}`;
          const started = Date.now();
          await page.goto(url);
          const settled = await page.waitForFunction(() => window.__bench || window.__qaError, null, { timeout: timeoutMs })
            .then(() => true, () => false);
          const { bench, error, state } = await page.evaluate(() => ({ bench: window.__bench, error: window.__qaError,
            state: { ready: window.__qaReady, load: window.__game?.loadProgress?.(), qa: window.__game?.qa, frames: window.__game?.clock,
              info: window.__qaInfo, perf: window.__perf } }));
          const label = `${id}-${at.replace(':', '')}-${quality}${repeat > 1 ? `-r${r + 1}` : ''}`;
          if (r === 0) {
            await page.evaluate(() => document.getElementById('bench-result')?.remove());
            await page.waitForTimeout(500); // rendering resumed after the drain: a fresh frame for the screenshot
            await page.screenshot({ path: path.join(out, `${label}.png`) });
          }
          const row = { label, url, wallSeconds: Math.round((Date.now() - started) / 100) / 10, loadavg: os.loadavg(),
            error: error ?? (settled ? null : `timeout after ${timeoutMs / 1000} s`), pageErrors: [...pageErrors].slice(0, 50),
            bench: bench ?? null, state: bench ? undefined : state };
          results.push(row);
          fs.writeFileSync(path.join(out, `${label}.json`), JSON.stringify(row, null, 1));
          const b = bench ?? {};
          console.log(`${label}: gpu p50 ${b.gpuMs?.p50?.toFixed?.(2)} p95 ${b.gpuMs?.p95?.toFixed?.(2)} cpu p50 ${b.cpuMs?.p50?.toFixed?.(2)} `
            + `draws ${b.submittedDraws?.total} tris ${b.triangles} pipelines ${b.pipelinesAfterMeasure} ${row.error ?? ''}`);
        }
      }
    }
  }
} finally {
  fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify({ chrome: context.browser()?.version() ?? null,
    upstream: upstream.href, traffic, results, host: { cpus: os.cpus()[0]?.model, platform: os.platform(), release: os.release() } }, null, 1));
  const f = v => (typeof v === 'number' ? v.toFixed(2) : '–');
  fs.writeFileSync(path.join(out, 'results.md'), [
    '| Run | Draws total (prepass / beauty / csm / reflection / post) | Instances | Triangles | GPU p50 / p95 ms | CPU p50 ms | Work p50 ms | Pipelines | Buffer | Load avg |',
    '|---|---|---:|---:|---|---:|---:|---:|---|---:|',
    ...results.map(({ label, bench: b, error, loadavg }) => b
      ? `| ${label} | ${b.submittedDraws.total} (${b.submittedDraws.prepass} / ${b.submittedDraws.beauty} / ${b.submittedDraws.csm} / `
        + `${b.submittedDraws.reflection} / ${b.submittedDraws.post}) | ${b.instances} | ${b.triangles} | ${f(b.gpuMs?.p50)} / ${f(b.gpuMs?.p95)} `
        + `| ${f(b.cpuMs?.p50)} | ${f(b.workMs?.p50)} | ${b.pipelinesAfterMeasure} | ${b.drawingBuffer?.join('×')} | ${loadavg[0].toFixed(1)} |`
      : `| ${label} | ${error} | | | | | | | | ${loadavg[0].toFixed(1)} |`),
  ].join('\n') + '\n');
  await context.close();
  server.kill();
  proxy.close();
}
console.log('traffic', JSON.stringify(traffic));
