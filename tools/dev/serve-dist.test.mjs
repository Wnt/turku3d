// node --test tools/dev/serve-dist.test.mjs
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import { port } from './environment.mjs';
// The facade candidate viewer is a serve-dist route; its handler tests run with these.
import './facadeCandidates.test.mjs';

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'serve-dist-'));
const tiles = fs.mkdtempSync(path.join(os.tmpdir(), 'serve-tiles-'));
fs.writeFileSync(path.join(dir, 'index.html'), '<!doctype html><title>x</title>');
const big = Buffer.alloc(100000, 7); big.write('TCOL', 0);
fs.mkdirSync(path.join(tiles, 'collision'));
fs.writeFileSync(path.join(tiles, 'collision', 'c_0_0.col'), big);
const PORT = port(3);
let srv;
before(async () => {
  srv = spawn(process.execPath, [new URL('./serve-dist.mjs', import.meta.url).pathname, '--root', dir, '--port', String(PORT), '--mount', `/tiles=${tiles}`, '--candidates', tiles]);
  await new Promise((res, reject) => {
    srv.once('exit', (code) => reject(new Error(`server exited: ${code}`)));
    srv.stdout.on('data', (d) => /serve-dist/.test(d) && res());
  });
});
after(() => srv.kill());
const url = (p) => `http://127.0.0.1:${PORT}${p}`;

test('COOP/COEP + html', async () => {
  const r = await fetch(url('/'));
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('cross-origin-opener-policy'), 'same-origin');
  assert.equal(r.headers.get('cross-origin-embedder-policy'), 'require-corp');
  assert.match(await r.text(), /<title>x/);
});
test('brotli on request; gzip whole with its length', async () => {
  const gz = await new Promise((res) => import('node:http').then(({ default: http }) => http.get(url('/tiles/collision/c_0_0.col'),
    { headers: { 'accept-encoding': 'gzip' } }, (x) => { const c = []; x.on('data', (d) => c.push(d)); x.on('end', () => res({ x, body: Buffer.concat(c) })); })));
  assert.equal(gz.x.headers['content-encoding'], 'gzip');
  assert.equal(Number(gz.x.headers['content-length']), gz.body.length);
  assert.ok(zlib.gunzipSync(gz.body).equals(big));
  const r = await fetch(url('/tiles/collision/c_0_0.col'), { headers: { 'accept-encoding': 'br' }, decompress: false });
  assert.equal(r.headers.get('content-encoding'), 'br');
  const raw = Buffer.from(await r.arrayBuffer());
  // undici may already decode; accept both
  const body = raw.length === big.length ? raw : zlib.brotliDecompressSync(raw);
  assert.ok(body.equals(big));
});
test('compressing a big file holds no other request, and concurrent requests share one compression', async () => {
  // 16 MB of random hex: brotli works on it for ~0.5 s (repetitive text compresses at ~1 GB/s and proves nothing).
  const text = crypto.randomBytes(8e6).toString('hex');
  fs.writeFileSync(path.join(dir, 'big.bin'), text);
  const t0 = performance.now();
  const big = [1, 2].map(() => fetch(url('/big.bin'), { headers: { 'accept-encoding': 'br' } }).then(async (r) => ({ at: performance.now() - t0, n: (await r.arrayBuffer()).byteLength })));
  await new Promise((r) => setTimeout(r, 30));
  const page = await fetch(url('/')).then(async (r) => { await r.text(); return performance.now() - t0; });
  const [a, b] = await Promise.all(big);
  assert.ok(page < a.at, `the page waited for the compression (${page.toFixed(0)} ms vs ${a.at.toFixed(0)} ms)`);
  assert.equal(a.n, text.length); assert.equal(b.n, text.length); // undici decodes; both whole
});
test('precompress writes brotli sidecars for big compressible files, which are served instead of compressing', async () => {
  const { precompress } = await import('./http.mjs');
  const pre = fs.mkdtempSync(path.join(dir, 'pre-'));
  const text = Array.from({ length: 40000 }, (_, i) => `line ${i} ${(i * 2654435761) % 1e9}`).join('\n'); // ~700 KB
  fs.writeFileSync(path.join(pre, 'big.js'), text);
  fs.writeFileSync(path.join(pre, 'small.js'), 'export const x = 1;');
  fs.writeFileSync(path.join(pre, 'big.ktx2'), Buffer.alloc(300000, 1));
  assert.equal(await precompress(pre).then((d) => d.files), 1);
  assert.deepEqual(fs.readdirSync(pre).sort(), ['big.js', 'big.js.br', 'big.ktx2', 'small.js']);
  assert.equal(await precompress(pre).then((d) => d.files), 0); // current sidecars stay
  const served = async () => {
    const r = await new Promise((res) => import('node:http').then(({ default: http }) => http.get(url(`/${path.basename(pre)}/big.js`),
      { headers: { 'accept-encoding': 'br' } }, (x) => { const c = []; x.on('data', (d) => c.push(d)); x.on('end', () => res(Buffer.concat(c))); })));
    return zlib.brotliDecompressSync(r).toString();
  };
  // A marked sidecar shows which body goes out: the sidecar while it is current, a fresh compression once it is stale.
  fs.writeFileSync(path.join(pre, 'big.js.br'), zlib.brotliCompressSync('from the sidecar'));
  assert.equal(await served(), 'from the sidecar');
  const later = new Date(Date.now() + 5000); fs.utimesSync(path.join(pre, 'big.js'), later, later);
  assert.equal(await served(), text);
});
test('/__localhost/status: a dist server is never the localhost game service', async () => {
  const r = await fetch(url('/__localhost/status'));
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { service: false });
});
test('range request', async () => {
  const r = await fetch(url('/tiles/collision/c_0_0.col'), { headers: { range: 'bytes=0-3' } });
  assert.equal(r.status, 206);
  assert.equal(r.headers.get('content-range'), `bytes 0-3/${big.length}`);
  assert.equal(Buffer.from(await r.arrayBuffer()).toString(), 'TCOL');
  const s = await fetch(url('/tiles/collision/c_0_0.col'), { headers: { range: 'bytes=-10' } });
  assert.equal(s.status, 206); assert.equal((await s.arrayBuffer()).byteLength, 10);
  const bad = await fetch(url('/tiles/collision/c_0_0.col'), { headers: { range: 'bytes=999999-' } });
  assert.equal(bad.status, 416);
});
test('404 and traversal', async () => {
  assert.equal((await fetch(url('/nope.js'))).status, 404);
  const sib = dir + '-sibling'; fs.mkdirSync(sib, { recursive: true }); fs.writeFileSync(path.join(sib, 'secret'), 's');
  const r = await new Promise((res) => {
    import('node:http').then(({ default: http }) => http.get({ host: '127.0.0.1', port: PORT, path: `/../${path.basename(sib)}/secret` }, (x) => res(x.statusCode)));
  });
  assert.notEqual(r, 200);
});
test("Vite's hashed build output is immutable for a year; the page and public assets revalidate", async () => {
  fs.mkdirSync(path.join(dir, 'assets', 'cars'), { recursive: true });
  for (const name of ['main-DF04okNI.js', 'decodeGlb.worker-Dh_bGai-.js', 'LICENSES.md', 'cars/model-12345678.glb']) fs.writeFileSync(path.join(dir, 'assets', name), 'x');
  const cache = async (p) => (await fetch(url(p))).headers.get('cache-control');
  assert.equal(await cache('/assets/main-DF04okNI.js'), 'public, max-age=31536000, immutable');
  assert.equal(await cache('/assets/decodeGlb.worker-Dh_bGai-.js?worker'), 'public, max-age=31536000, immutable');
  assert.equal(await cache('/'), 'no-cache');
  assert.equal(await cache('/assets/LICENSES.md'), 'public, max-age=60');
  assert.equal(await cache('/assets/cars/model-12345678.glb'), 'public, max-age=60'); // public files keep their names
});
test('wasm mime + etag 304', async () => {
  fs.writeFileSync(path.join(dir, 'a.wasm'), Buffer.from([0, 97, 115, 109]));
  const r = await fetch(url('/a.wasm'));
  assert.equal(r.headers.get('content-type'), 'application/wasm');
  const r2 = await fetch(url('/a.wasm'), { headers: { 'if-none-match': r.headers.get('etag') } });
  assert.equal(r2.status, 304);
});


test('two dist servers use independent environment ports and output roots', async () => {
  const servers = [];
  const roots = [];
  try {
    await Promise.all([6, 7].map(async (offset) => {
      const work = fs.mkdtempSync(path.join(os.tmpdir(), 'isolated-dist-'));
      roots.push(work);
      fs.mkdirSync(path.join(work, 'dist'));
      fs.writeFileSync(path.join(work, 'dist/index.html'), `sandbox-${offset}`);
      const child = spawn(process.execPath, [new URL('./serve-dist.mjs', import.meta.url).pathname], {
        env: { ...process.env, TURKU3D_WORK: work, TURKU3D_PORT_BASE: String(port(offset) - 1) },
      });
      servers.push(child);
      await new Promise((resolve, reject) => {
        child.once('exit', (code) => reject(new Error(`server exited: ${code}`)));
        child.stdout.on('data', (d) => /serve-dist/.test(d) && resolve());
      });
      assert.equal(await (await fetch(`http://127.0.0.1:${port(offset)}/`)).text(), `sandbox-${offset}`);
    }));
  } finally {
    await Promise.all(servers.map((child) => new Promise((resolve) => { child.once('exit', resolve); child.kill(); })));
    roots.forEach((dir) => fs.rmSync(dir, { recursive: true, force: true }));
  }
});

test('POST /telemetry appends JSON lines to the day file', async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'telemetry-'));
  const child = spawn(process.execPath, [new URL('./serve-dist.mjs', import.meta.url).pathname, '--root', dir, '--port', String(port(8))], {
    env: { ...process.env, TURKU3D_WORK: work },
  });
  try {
    await new Promise((resolve, reject) => {
      child.once('exit', (code) => reject(new Error(`server exited: ${code}`)));
      child.stdout.on('data', (d) => /serve-dist/.test(d) && resolve());
    });
    const endpoint = `http://127.0.0.1:${port(8)}/telemetry`;
    const post = (body) => fetch(endpoint, { method: 'POST', body, headers: { 'content-type': 'application/x-ndjson' } });
    const ok = await post('{"t":1,"facades":[]}\n{"t":2,"facades":[]}\n');
    assert.equal(ok.status, 204);
    assert.equal(ok.headers.get('access-control-allow-origin'), '*');
    assert.equal((await post('{"t":3}')).status, 204); // a final line without newline
    assert.equal((await post('{"t":4}\nnot json\n')).status, 400);
    assert.equal((await post('[1]\n')).status, 400);
    assert.equal((await post('x'.repeat(1024 * 1024 + 1))).status, 413);
    const preflight = await fetch(endpoint, { method: 'OPTIONS' });
    assert.match(preflight.headers.get('access-control-allow-methods'), /POST/);
    assert.equal((await fetch(`http://127.0.0.1:${port(8)}/index.html`, { method: 'POST' })).status, 405);
    const d = new Date();
    const day = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`;
    const lines = fs.readFileSync(path.join(work, 'telemetry', `session-${day}.jsonl`), 'utf8').trimEnd().split('\n');
    assert.deepEqual(lines.map((l) => JSON.parse(l).t), [1, 2, 3]);
  } finally {
    await new Promise((resolve) => { child.once('exit', resolve); child.kill(); });
    fs.rmSync(work, { recursive: true, force: true });
  }
});

test('/coarse/* is proxied to the coarse tile service; offline status when it is not running', async () => {
  const http = (await import('node:http')).default;
  const upstream = http.createServer((req, res) => {
    res.writeHead(req.url.startsWith('/coarse/t0_1_1/') ? 202 : 200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ path: req.url }));
  });
  await new Promise((r) => upstream.listen(0, '127.0.0.1', r));
  const target = `http://127.0.0.1:${upstream.address().port}`;
  const closed = http.createServer();
  await new Promise((r) => closed.listen(0, '127.0.0.1', r));
  const offline = `http://127.0.0.1:${closed.address().port}`;
  await new Promise((r) => closed.close(r));
  const servers = [];
  try {
    for (const [offset, coarse] of [[8, target], [9, offline]]) {
      const child = spawn(process.execPath, [new URL('./serve-dist.mjs', import.meta.url).pathname, '--root', dir,
        '--port', String(port(offset)), '--coarse', coarse]);
      servers.push(child);
      await new Promise((resolve, reject) => {
        child.once('exit', (code) => reject(new Error(`server exited: ${code}`)));
        child.stdout.on('data', (d) => /serve-dist/.test(d) && resolve());
      });
    }
    const ok = await fetch(`http://127.0.0.1:${port(8)}/coarse/t0_1_1/manifest.json?d=5`);
    assert.equal(ok.status, 202);
    assert.equal(ok.headers.get('cross-origin-embedder-policy'), 'require-corp');
    assert.deepEqual(await ok.json(), { path: '/coarse/t0_1_1/manifest.json?d=5' });
    assert.equal((await fetch(`http://127.0.0.1:${port(8)}/coarse/status`)).status, 200);
    const down = await fetch(`http://127.0.0.1:${port(9)}/coarse/status`);
    assert.equal(down.status, 200);
    assert.deepEqual(await down.json(), { state: 'offline' });
    assert.equal((await fetch(`http://127.0.0.1:${port(9)}/coarse/t0_1_1/vis.glb`)).status, 503);
  } finally {
    await Promise.all(servers.map((child) => new Promise((resolve) => { child.once('exit', resolve); child.kill(); })));
    await new Promise((r) => upstream.close(r));
  }
});
test('facade candidate viewer', async () => {
  const r = await fetch(url('/facades/candidates/'));
  assert.equal(r.status, 200);
  assert.match(await r.text(), /Generated facade candidates[\s\S]*No candidates kept yet/);
});

test('--public serves only the game and its services: no uploads, internals or dotfiles; compressed tiles; multiplayer', async () => {
  const http = (await import('node:http')).default;
  const glb = Buffer.alloc(200000, 3); glb.write('glTF', 0);
  const seen = [];
  const coarse = http.createServer((req, res) => {
    seen.push(`${req.method} ${req.url}`);
    if (/\/(health|demand)$/.test(req.url.split('?')[0])) {
      res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
      return res.end(JSON.stringify({ state: 'ready', version: 7, building: ['t0_0_0'], failures: [{ error: '/home/x' }] }));
    }
    if (req.url.endsWith('/cut.glb')) { // a restart mid-body: the length is declared, the bytes stop
      res.writeHead(200, { 'Content-Type': 'model/gltf-binary', 'Content-Length': glb.length, 'Cache-Control': 'public,max-age=31536000,immutable' });
      return res.write(glb.subarray(0, 50000), () => res.socket.destroy());
    }
    if (req.url.endsWith('/unframed.glb')) { // no length, not chunked: the body ends when the connection does
      res.socket.write('HTTP/1.1 200 OK\r\nContent-Type: model/gltf-binary\r\nCache-Control: public,max-age=31536000,immutable\r\nConnection: close\r\n\r\n');
      return res.socket.end(glb);
    }
    res.writeHead(200, { 'Content-Type': 'model/gltf-binary', 'Content-Length': glb.length,
      ...(req.url.includes('/_v/') ? { 'Cache-Control': 'public,max-age=31536000,immutable' } : {}) });
    res.end(glb);
  });
  const mp = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ path: req.url, origin: req.headers.origin ?? null }));
  });
  const upgraded = [];
  mp.on('upgrade', (req, socket) => {
    upgraded.push(socket);
    socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nX-Origin: ${req.headers.origin}\r\nX-Path: ${req.url}\r\n\r\n`);
    socket.on('data', (d) => socket.write(d)); // echo
  });
  await Promise.all([coarse, mp].map((s) => new Promise((r) => s.listen(0, '127.0.0.1', r))));
  fs.writeFileSync(path.join(dir, '.secret'), 's');
  fs.writeFileSync(path.join(dir, 'version.json'), '{"sha":"abc"}');
  const child = spawn(process.execPath, [new URL('./serve-dist.mjs', import.meta.url).pathname, '--root', dir, '--port', '0',
    '--public', '--coarse', `http://127.0.0.1:${coarse.address().port}`, '--mp', `http://127.0.0.1:${mp.address().port}`,
    '--restart-wait-ms', '400']);
  try {
    const base = await new Promise((resolve, reject) => {
      child.once('exit', (code) => reject(new Error(`server exited: ${code}`)));
      child.stdout.on('data', (d) => { const m = /serve-dist: (http:\/\/[^/]+)\//.exec(String(d)); if (m) resolve(m[1]); });
    });
    const get = (p, init) => fetch(base + p, init);
    const home = await get('/');
    assert.equal(home.status, 200);
    assert.equal(home.headers.get('cross-origin-embedder-policy'), 'require-corp');
    assert.equal(home.headers.get('cache-control'), 'no-cache');
    fs.mkdirSync(path.join(dir, 'assets'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'assets', 'city-BqGuNPKr.js'), 'x');
    assert.equal((await get('/assets/city-BqGuNPKr.js')).headers.get('cache-control'), 'public, max-age=31536000, immutable');
    assert.deepEqual(await (await get('/version.json')).json(), { sha: 'abc' });
    assert.equal((await get('/telemetry', { method: 'POST', body: '{"t":1}\n' })).status, 405);
    assert.equal((await get('/__capture/frame', { method: 'POST', body: 'x' })).status, 405);
    assert.equal((await get('/facades/candidates/')).status, 404);
    assert.equal((await get('/.secret')).status, 404);
    // A service status keeps only what the game reads.
    const health = await get('/coarse/health');
    assert.equal(health.status, 200);
    assert.deepEqual(await health.json(), { state: 'ready', version: 7 });
    assert.deepEqual(await (await get('/coarse/demand?client=a&tiles=t0_0_0,t0_1_0')).json(), { state: 'ready', version: 7 });
    assert.ok(seen.includes('GET /coarse/demand?client=a&tiles=t0_0_0,t0_1_0'));
    // Raw paths: fetch() would normalise the dot segments before sending them.
    const raw = (p) => new Promise((res) => http.get({ host: '127.0.0.1', port: new URL(base).port, path: p }, (x) => { x.resume(); res(x.statusCode); }));
    for (const p of ['/coarse/%2e%2e/%2e%2e/health', '/coarse/%2E%2E/%2E%2E/status', '/coarse/../../health', '/coarse/./health',
      '/coarse/t0_0_0/.x', '/coarse/t0_0_0%2f..%2f..%2fhealth', '/coarse/t0_0_0\\..\\health']) assert.equal(await raw(p), 404, p);
    const before = seen.length;
    assert.equal(await raw('/coarse/t0_0_0/manifest.json?d=1'), 200);
    assert.equal(seen.length, before + 1);
    assert.equal((await get('/coarse/t0_0_0/manifest.json', { method: 'POST', body: '{}' })).status, 405);
    // Payloads are compressed for the tunnel and decode to the same bytes.
    const tile = await new Promise((res) => http.get({ host: '127.0.0.1', port: new URL(base).port, path: '/coarse/t0_0_0/a.glb',
      headers: { 'accept-encoding': 'br' } }, (x) => { const c = []; x.on('data', (d) => c.push(d)); x.on('end', () => res({ x, body: Buffer.concat(c) })); }));
    assert.equal(tile.x.headers['content-encoding'], 'br');
    assert.equal(tile.x.headers['cross-origin-resource-policy'], 'cross-origin');
    assert.equal(Number(tile.x.headers['content-length']), tile.body.length, 'an encoded payload carries its exact length');
    assert.ok(tile.body.length < glb.length / 10);
    assert.ok(zlib.brotliDecompressSync(tile.body).equals(glb));
    // A revision-named (immutable) payload is encoded once and then served from memory for every player.
    const fetchRaw = (p) => new Promise((res) => http.get({ host: '127.0.0.1', port: new URL(base).port, path: p,
      headers: { 'accept-encoding': 'br' } }, (x) => { const c = []; x.on('data', (d) => c.push(d)); x.on('end', () => res({ x, body: Buffer.concat(c) })); }));
    const pinnedGlb = `/coarse/_v/${'a'.repeat(64)}/t0_0_0/vis.${'b'.repeat(64)}.glb`;
    const first = await fetchRaw(pinnedGlb);
    const asked = seen.length;
    const again = await fetchRaw(pinnedGlb);
    assert.equal(seen.length, asked);
    assert.equal(again.x.headers['content-encoding'], 'br');
    assert.equal(again.x.headers['cache-control'], 'public,max-age=31536000,immutable');
    assert.ok(again.body.equals(first.body));
    assert.equal(Number(first.x.headers['content-length']), first.body.length);
    assert.equal(Number(again.x.headers['content-length']), again.body.length);
    await fetchRaw('/coarse/t0_0_0/a.glb');
    assert.equal(seen.length, asked + 1, 'a mutable payload is not kept');
    // An immutable payload the upstream cut short is a retryable 502, never a shorter complete answer, and not kept.
    const cutGlb = `/coarse/_v/${'a'.repeat(64)}/t0_0_0/cut.glb`;
    const cut = await fetchRaw(cutGlb);
    assert.equal(cut.x.statusCode, 502);
    assert.equal(cut.x.headers['cache-control'], 'no-store');
    const cutAsked = seen.length;
    await fetchRaw(cutGlb);
    assert.ok(seen.length > cutAsked + 1, 'a cut payload is asked again, also within the restart wait');
    // A body that only ends with its connection cannot be told from a cut one: served whole, never cached.
    const unframedGlb = `/coarse/_v/${'a'.repeat(64)}/t0_0_0/unframed.glb`;
    const unframed = await fetchRaw(unframedGlb);
    assert.equal(unframed.x.statusCode, 200);
    assert.equal(unframed.x.headers['cache-control'], 'no-cache');
    assert.equal(Number(unframed.x.headers['content-length']), unframed.body.length);
    assert.ok(zlib.brotliDecompressSync(unframed.body).equals(glb));
    const unframedAsked = seen.length;
    await fetchRaw(unframedGlb);
    assert.equal(seen.length, unframedAsked + 1, 'an unframed payload is not kept');
    // Scores and the socket reach the share's own multiplayer server.
    assert.deepEqual(await (await get('/live.json', { headers: { origin: 'https://city.example' } })).json(), { path: '/live.json', origin: 'https://city.example' });
    assert.equal((await (await get('/scores/index.json')).json()).path, '/scores/index.json');
    const net = (await import('node:net')).default;
    const echoed = await new Promise((resolve, reject) => {
      const s = net.connect(Number(new URL(base).port), '127.0.0.1', () => s.write('GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nOrigin: https://city.example\r\n\r\n'));
      let text = '';
      s.on('data', (d) => {
        text += d;
        if (text.includes('\r\n\r\n') && !text.includes('ping')) s.write('ping');
        if (text.endsWith('ping')) { s.destroy(); resolve(text); }
      });
      s.on('error', reject);
    });
    assert.match(echoed, /^HTTP\/1.1 101/);
    assert.match(echoed, /X-Origin: https:\/\/city.example/);
  } finally {
    await new Promise((resolve) => { child.once('exit', resolve); child.kill(); });
    upgraded.forEach((s) => s.destroy());
    await Promise.all([coarse, mp].map((s) => new Promise((r) => { s.closeAllConnections?.(); s.close(r); })));
    fs.rmSync(path.join(dir, '.secret')); fs.rmSync(path.join(dir, 'version.json'));
  }
});

test('under systemd it accepts on the inherited socket, and SIGTERM lets the request in flight finish', async () => {
  const http = (await import('node:http')).default;
  const glb = Buffer.alloc(300000, 9); glb.write('glTF', 0);
  const slow = http.createServer((req, res) => setTimeout(() => {
    res.writeHead(200, { 'Content-Type': 'model/gltf-binary', 'Content-Length': glb.length, 'Cache-Control': 'public,max-age=31536000,immutable' });
    res.end(glb);
  }, 800));
  await new Promise((r) => slow.listen(0, '127.0.0.1', r));
  // The socket systemd would hold: bound and listening before the server starts, handed over as fd 3.
  const activate = [
    'import os, socket, sys',
    's = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(64)',
    'print(s.getsockname()[1], flush=True)',
    'os.dup2(s.fileno(), 3); os.set_inheritable(3, True)',
    'os.environ["LISTEN_FDS"] = "1"; os.environ.pop("LISTEN_PID", None)',
    'os.execv(sys.argv[1], sys.argv[1:])',
  ].join('\n');
  const child = spawn('python3', ['-c', activate, process.execPath, new URL('./serve-dist.mjs', import.meta.url).pathname,
    '--root', dir, '--public', '--coarse', `http://127.0.0.1:${slow.address().port}`]);
  const exited = new Promise((resolve) => child.once('exit', (code) => resolve(code)));
  try {
    const port = await new Promise((resolve, reject) => {
      let text = '';
      child.once('exit', (code) => reject(new Error(`server exited: ${code}`)));
      child.stdout.on('data', (d) => {
        text += d;
        if (/serve-dist: .*\(systemd socket\)/.test(text)) resolve(Number(text.split('\n')[0]));
      });
    });
    const answer = new Promise((resolve) => http.get({ host: '127.0.0.1', port, path: `/coarse/_v/${'a'.repeat(64)}/t0_0_0/vis.glb`,
      headers: { 'accept-encoding': 'br' } }, (x) => { const c = []; x.on('data', (d) => c.push(d)); x.on('end', () => resolve({ x, body: Buffer.concat(c) })); }));
    await new Promise((r) => setTimeout(r, 200));
    const stopped = Date.now();
    child.kill('SIGTERM'); // a restart while the payload is in flight
    const { x, body } = await answer;
    assert.equal(x.statusCode, 200);
    assert.equal(Number(x.headers['content-length']), body.length);
    assert.ok(zlib.brotliDecompressSync(body).equals(glb), 'the drained answer is whole');
    assert.equal(await exited, 0);
    assert.ok(Date.now() - stopped < 3000, 'the drain ends with the last request, not at its cap');
  } finally {
    child.kill('SIGKILL');
    await new Promise((r) => { slow.closeAllConnections?.(); slow.close(r); });
  }
});

test('a tile service that restarts is waited out: a payload GET retries a refused upstream until it answers', async () => {
  const http = (await import('node:http')).default;
  const net = (await import('node:net')).default;
  const glb = Buffer.alloc(50000, 4); glb.write('glTF', 0);
  const port = await new Promise((r) => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => r(p)); }); });
  const child = spawn(process.execPath, [new URL('./serve-dist.mjs', import.meta.url).pathname, '--root', dir, '--port', '0',
    '--public', '--coarse', `http://127.0.0.1:${port}`, '--restart-wait-ms', '5000']);
  let upstream;
  try {
    const base = await new Promise((resolve, reject) => {
      child.once('exit', (code) => reject(new Error(`server exited: ${code}`)));
      child.stdout.on('data', (d) => { const m = /serve-dist: (http:\/\/[^/]+)\//.exec(String(d)); if (m) resolve(m[1]); });
    });
    const started = Date.now();
    const answer = new Promise((res) => http.get(`${base}/coarse/_v/${'a'.repeat(64)}/t0_0_0/vis.glb`,
      { headers: { 'accept-encoding': 'br' } }, (x) => { const c = []; x.on('data', (d) => c.push(d)); x.on('end', () => res({ x, body: Buffer.concat(c) })); }));
    await new Promise((r) => setTimeout(r, 700)); // the service comes back after its restart
    upstream = http.createServer((req, res) => { res.writeHead(200, { 'Content-Type': 'model/gltf-binary', 'Content-Length': glb.length }); res.end(glb); });
    await new Promise((r) => upstream.listen(port, '127.0.0.1', r));
    const { x, body } = await answer;
    assert.equal(x.statusCode, 200);
    assert.ok(zlib.brotliDecompressSync(body).equals(glb));
    assert.ok(Date.now() - started >= 700, 'the answer waited for the restarted service');
  } finally {
    await new Promise((resolve) => { child.once('exit', resolve); child.kill(); });
    if (upstream) await new Promise((r) => { upstream.closeAllConnections?.(); upstream.close(r); });
  }
});
