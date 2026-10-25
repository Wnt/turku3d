// node --test tools/dev/serve-dist.test.mjs
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import { port } from './environment.mjs';

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'serve-dist-'));
const tiles = fs.mkdtempSync(path.join(os.tmpdir(), 'serve-tiles-'));
fs.writeFileSync(path.join(dir, 'index.html'), '<!doctype html><title>x</title>');
const big = Buffer.alloc(100000, 7); big.write('TCOL', 0);
fs.mkdirSync(path.join(tiles, 'collision'));
fs.writeFileSync(path.join(tiles, 'collision', 'c_0_0.col'), big);
const PORT = port(3);
let srv;
before(async () => {
  srv = spawn(process.execPath, [new URL('./serve-dist.mjs', import.meta.url).pathname, '--root', dir, '--port', String(PORT), '--mount', `/tiles=${tiles}`]);
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
test('brotli on request', async () => {
  const r = await fetch(url('/tiles/collision/c_0_0.col'), { headers: { 'accept-encoding': 'br' }, decompress: false });
  assert.equal(r.headers.get('content-encoding'), 'br');
  const raw = Buffer.from(await r.arrayBuffer());
  // undici may already decode; accept both
  const body = raw.length === big.length ? raw : zlib.brotliDecompressSync(raw);
  assert.ok(body.equals(big));
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
    await Promise.all([4, 5].map(async (offset) => {
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
