import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createSharedTileProxy } from './shared-tile-proxy.mjs';

const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const close = server => new Promise(resolve => server.close(resolve));

test('hot alias selection, immutable payload routing and scenery stay independent', async () => {
  const rev = 'a'.repeat(40);
  const calls = [];
  const upstream = http.createServer((req, res) => {
    calls.push(req.url);
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify({ url: `/v/${rev}/coarse/t0_0_0/mesh.glb` }));
  });
  await listen(upstream);
  const origin = `http://127.0.0.1:${upstream.address().port}`;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tile-proxy-'));
  const file = path.join(dir, 'config.json');
  const write = version => fs.writeFileSync(file, JSON.stringify({ service: origin, scenery: origin, version }));
  write('ground');
  const proxy = createSharedTileProxy(file);
  await listen(proxy);
  const base = `http://127.0.0.1:${proxy.address().port}`;
  try {
    const fragment = await (await fetch(base + '/coarse/t0_0_0/manifest.json?d=0')).json();
    assert.equal(calls.pop(), '/v/ground/coarse/t0_0_0/manifest.json?d=0');
    assert.equal(fragment.url, `/coarse/_v/${rev}/t0_0_0/mesh.glb`);
    write('integration');
    await fetch(base + fragment.url);
    assert.equal(calls.pop(), `/v/${rev}/coarse/t0_0_0/mesh.glb`);
    await fetch(base + '/coarse/status');
    assert.equal(calls.pop(), '/v/integration/coarse/status');
    await fetch(base + '/coarse/demand?client=play&tiles=t0_0_0,t0_1_0&urgent=t0_0_0&visible=t0_1_0');
    // Demand is answered at once and forwarded behind the answer.
    for (let i = 0; i < 50 && !calls.at(-1)?.includes('/demand'); i++) await new Promise(r => setTimeout(r, 10));
    assert.equal(calls.pop(), '/v/integration/coarse/demand?client=play&tiles=t0_0_0,t0_1_0&urgent=t0_0_0&visible=t0_1_0');
    await fetch(base + '/coarse/want?tiles=t0_0_0');
    assert.equal(calls.pop(), '/v/integration/coarse/want?tiles=t0_0_0');
    await fetch(base + '/coarse/scenery/index.json');
    assert.equal(calls.pop(), '/coarse/scenery/index.json');
  } finally {
    await close(proxy); await close(upstream); fs.rmSync(dir, { recursive: true });
  }
});

test('recovers transient asset failures without retrying writes or retrying indefinitely', async () => {
  let calls = 0, recover = true;
  const upstream = http.createServer((request, response) => {
    calls++;
    if (recover && calls === 1) { request.socket.destroy(); return; }
    if (recover && calls === 3) { response.end('complete texture'); return; }
    response.writeHead(502); response.end('temporary upstream failure');
  });
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tile-proxy-test-'));
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const config = path.join(root, 'config.json');
  fs.writeFileSync(config, JSON.stringify({ service: `http://127.0.0.1:${upstream.address().port}`, version: 'fixture' }));
  const proxy = createSharedTileProxy(config);
  await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${proxy.address().port}/coarse`;
  try {
    const recovered = await fetch(`${base}/shared/map.ktx2`);
    assert.equal(await recovered.text(), 'complete texture');
    assert.equal(calls, 3);
    recover = false; calls = 0;
    assert.equal((await fetch(`${base}/shared/map.ktx2`)).status, 502);
    assert.equal(calls, 4);
    calls = 0;
    assert.equal((await fetch(`${base}/want`, { method: 'POST', body: 'demand' })).status, 502);
    assert.equal(calls, 1);
  } finally {
    proxy.closeAllConnections(); upstream.closeAllConnections();
    await Promise.all([new Promise(resolve => proxy.close(resolve)), new Promise(resolve => upstream.close(resolve))]);
    fs.rmSync(root, { recursive: true });
  }
});

test('each tile comes from the newest compatible train that finished it, else the pin', async () => {
  const calls = [];
  let batched = false;
  const base = 'a'.repeat(64), other = 'f'.repeat(64);
  const aliases = { pin: 'p', train1: 't1', train2: 't2', 'train0-probe': 'x', train9: 't9', lidarwires: 'l' };
  // finished[version] = tiles with a ready.json; train9 sits on another (structural) generation.
  const finished = { pin: ['t0_0_0', 't0_1_0', 't0_2_0'], train1: ['t0_0_0', 't0_1_0'], train2: ['t0_0_0'], train9: ['t0_0_0', 't0_1_0', 't0_2_0'],
    'train0-probe': ['t0_2_0'] };
  const upstream = http.createServer((req, res) => {
    calls.push(`${req.method} ${req.url}`);
    res.setHeader('content-type', 'application/json');
    if (req.url === '/status') { res.end(JSON.stringify({ aliases })); return; }
    const receipt = (version, tile) => finished[version]?.includes(tile)
      ? { generation: version, base_generation: version === 'train9' ? other : base } : null;
    const batch = new URL(req.url, 'http://x');
    if (batch.pathname === '/receipts') {
      if (!batched) { res.writeHead(404); res.end('{}'); return; }
      const tile = batch.searchParams.get('tile');
      res.end(JSON.stringify(Object.fromEntries(batch.searchParams.get('v').split(',').map(v => [v, receipt(v, tile)])))); return;
    }
    const ready = /^\/v\/([^/]+)\/coarse\/([^/]+)\/ready\.json$/.exec(req.url);
    if (ready) {
      const [, version, tile] = ready;
      if (!finished[version]?.includes(tile)) { res.writeHead(404); res.end('{}'); return; }
      res.end(JSON.stringify({ generation: version, base_generation: version === 'train9' ? other : base })); return;
    }
    const version = /^\/v\/([^/]+)\//.exec(req.url)[1];
    res.end(JSON.stringify({ url: `/v/${version === 'pin' ? 'c'.repeat(64) : 'b'.repeat(64)}/coarse/t/mesh.glb`, version }));
  });
  await listen(upstream);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tile-proxy-chain-'));
  const file = path.join(dir, 'config.json');
  const write = extra => fs.writeFileSync(file, JSON.stringify({ service: `http://127.0.0.1:${upstream.address().port}`, version: 'pin', follow: 'train', ...extra }));
  write({ exclude: ['train9'] });
  const proxy = createSharedTileProxy(file);
  await listen(proxy);
  const origin = `http://127.0.0.1:${proxy.address().port}`;
  const served = async tile => (await (await fetch(`${origin}/coarse/${tile}/manifest.json?d=0`)).json()).version;
  try {
    assert.equal(await served('t0_0_0'), 'train2');
    assert.equal(await served('t0_1_0'), 'train1');
    assert.equal(await served('t0_2_0'), 'pin');  // train0-probe is not a train
    assert.ok(calls.every(c => !c.includes('train0-probe')));
    assert.ok(calls.filter(c => !c.endsWith('ready.json') && !c.startsWith('GET /receipts?') && c !== 'GET /status').every(c => /^GET \/v\/(pin|train1|train2)\/coarse\/t0_\d_0\/manifest\.json\?d=0$/.test(c)));
    write({});  // not excluded: a train on another base generation is still never mixed in
    assert.equal(await served('t0_1_0'), 'train1');
    calls.length = 0;
    await fetch(`${origin}/coarse/demand?client=play&tiles=t0_0_0`);
    await fetch(`${origin}/coarse/navigation.json`);
    // Demand goes out behind its answer, so its order against other requests is not fixed.
    for (let i = 0; i < 50 && calls.length < 2; i++) await new Promise(r => setTimeout(r, 10));
    assert.deepEqual(calls.sort(), ['GET /v/pin/coarse/demand?client=play&tiles=t0_0_0', 'GET /v/pin/coarse/navigation.json']);
    const fragment = await (await fetch(`${origin}/coarse/t0_0_0/collision.json?d=0`)).json();
    assert.equal(fragment.url, `/coarse/_v/${'b'.repeat(64)}/t/mesh.glb`);
    // Within CHOSEN_MS a tile's choice is reused without asking the farm again.
    calls.length = 0;
    write({ exclude: ['train9'] });
    assert.equal(await served('t0_0_0'), 'train2');
    assert.equal(calls.filter(c => c.endsWith('ready.json') || c.startsWith('GET /receipts?')).length, 0);
    // A farm with /receipts answers the whole chain in one request, with the same choice (a fresh proxy: no reuse).
    batched = true; calls.length = 0;
    const fresh = createSharedTileProxy(file);
    await listen(fresh);
    const freshOrigin = `http://127.0.0.1:${fresh.address().port}`;
    const servedFresh = async tile => (await (await fetch(`${freshOrigin}/coarse/${tile}/manifest.json?d=0`)).json()).version;
    try {
      assert.deepEqual([await servedFresh('t0_0_0'), await servedFresh('t0_1_0'), await servedFresh('t0_2_0')], ['train2', 'train1', 'pin']);
      assert.equal(calls.filter(c => c.endsWith('ready.json')).length, 0);
      assert.equal(calls.filter(c => c.startsWith('GET /receipts?')).length, 3);
    } finally {
      await close(fresh);
    }
  } finally {
    await close(proxy); upstream.closeAllConnections(); await close(upstream); fs.rmSync(dir, { recursive: true });
  }
});

test('a farm whose receipts do not answer is not waited on: the newest version known ready is served', async () => {
  let receiptsUp = true;
  const upstream = http.createServer((req, res) => {
    res.setHeader('content-type', 'application/json');
    if (req.url === '/status') { res.end(JSON.stringify({ aliases: { pin: 'p', train1: 't1' } })); return; }
    const url = new URL(req.url, 'http://x');
    if (url.pathname === '/receipts') {
      if (!receiptsUp) { res.writeHead(503); res.end('{}'); return; }
      res.end(JSON.stringify({ pin: { generation: 'p', base_generation: 'b' }, train1: { generation: 't1', base_generation: 'b' } }));
      return;
    }
    if (url.pathname.endsWith('/ready.json')) { res.writeHead(503); res.end('{}'); return; }  // the per-version path is down too
    res.end(JSON.stringify({ version: /^\/v\/([^/]+)\//.exec(req.url)[1] }));
  });
  await listen(upstream);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tile-proxy-fallback-'));
  const file = path.join(dir, 'config.json');
  fs.writeFileSync(file, JSON.stringify({ service: `http://127.0.0.1:${upstream.address().port}`, version: 'pin', follow: 'train' }));
  const proxy = createSharedTileProxy(file);
  await listen(proxy);
  const origin = `http://127.0.0.1:${proxy.address().port}`;
  const served = async tile => (await (await fetch(`${origin}/coarse/${tile}/manifest.json?d=0`)).json()).version;
  try {
    assert.equal(await served('t0_0_0'), 'train1');
    receiptsUp = false;
    const realNow = Date.now;
    Date.now = () => realNow() + 60_000;  // past CHOSEN_MS: the farm is asked again and fails
    try {
      assert.equal(await served('t0_0_0'), 'train1');  // the last version known ready, not the pin's rebuild path
    } finally {
      Date.now = realNow;
    }
  } finally {
    await close(proxy); upstream.closeAllConnections(); await close(upstream); fs.rmSync(dir, { recursive: true });
  }
});

test('finished fragments are shared for a while across distances and players; polls revalidate with an ETag', async () => {
  const calls = [];
  let state = 'queued';
  const upstream = http.createServer((req, res) => {
    calls.push(req.url);
    res.setHeader('content-type', 'application/json');
    setTimeout(() => res.end(JSON.stringify({ coarse: true, tile: 't0_0_0', stale: false, queue_state: state })), 20);
  });
  await listen(upstream);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tile-proxy-'));
  const file = path.join(dir, 'config.json');
  fs.writeFileSync(file, JSON.stringify({ service: `http://127.0.0.1:${upstream.address().port}`, version: 'pin' }));
  const proxy = createSharedTileProxy(file);
  await listen(proxy);
  const base = `http://127.0.0.1:${proxy.address().port}/coarse/t0_0_0/manifest.json`;
  try {
    await fetch(`${base}?d=50`); await fetch(`${base}?d=50`);
    assert.equal(calls.length, 1, 'an upgrading answer is shared briefly');
    state = 'built';
    await new Promise(resolve => setTimeout(resolve, 3100));
    calls.length = 0;
    const answers = await Promise.all([`${base}?d=50`, `${base}?d=80&client=b`, `${base}?d=120`].map(u => fetch(u)));
    assert.equal(calls.length, 1, 'the upgrade is polled again, and concurrent misses share one farm request');
    const etag = answers[0].headers.get('etag');
    assert.match(etag, /^W\/"/);
    assert.deepEqual(await answers[1].json(), { coarse: true, tile: 't0_0_0', stale: false, queue_state: 'built' });
    assert.equal((await fetch(`${base}?d=300`)).status, 200);
    assert.equal((await fetch(`${base}?d=300`, { headers: { 'if-none-match': etag } })).status, 304);
    assert.equal(calls.length, 1);
    await fetch(`${base}?d=0`);
    assert.equal(calls.length, 2, "the player's own tile still reaches the farm");
    await fetch(base.replace('manifest', 'collision') + '?d=50');
    assert.equal(calls.length, 3, 'collision and manifest are separate fragments');
  } finally {
    proxy.closeAllConnections(); upstream.closeAllConnections();
    await close(proxy); await close(upstream); fs.rmSync(dir, { recursive: true });
  }
});

test('health and status come from a short cache refreshed in the background, not one farm call per poll', async () => {
  let calls = 0;
  const upstream = http.createServer((req, res) => {
    calls++;
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify({ state: 'ready', call: calls }));
  });
  await listen(upstream);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tile-proxy-health-'));
  const file = path.join(dir, 'config.json');
  fs.writeFileSync(file, JSON.stringify({ service: `http://127.0.0.1:${upstream.address().port}`, version: 'pin' }));
  const proxy = createSharedTileProxy(file);
  await listen(proxy);
  const base = `http://127.0.0.1:${proxy.address().port}`;
  try {
    const first = await (await fetch(base + '/coarse/health')).json();
    assert.equal(first.state, 'ready');
    const answers = await Promise.all(Array.from({ length: 20 }, () => fetch(base + '/coarse/health').then(r => r.json())));
    assert.ok(answers.every(a => a.call === first.call), 'polls within 15 s share one farm answer');
    assert.equal(calls, 1);
  } finally {
    await close(proxy); await close(upstream); fs.rmSync(dir, { recursive: true });
  }
});

test('demand is answered at once and forwarded one at a time per client; scenery queries are served while revalidated', async () => {
  let hold = null, demandCalls = [], sceneryCalls = 0, sceneryKey = 'k1';
  const upstream = http.createServer((req, res) => {
    res.setHeader('content-type', 'application/json');
    if (req.url.includes('/demand')) {
      demandCalls.push(req.url);
      // The farm stalls on the first demand.
      if (demandCalls.length === 1) { hold = () => res.end(JSON.stringify({ version: 7 })); return; }
      res.end(JSON.stringify({ version: 8 })); return;
    }
    if (req.url.startsWith('/coarse/scenery?')) { sceneryCalls++; res.end(JSON.stringify({ tiles: [{ tile: 't2_1_-1', key: sceneryKey }], missing: [] })); return; }
    res.end('{}');
  });
  await listen(upstream);
  const origin = `http://127.0.0.1:${upstream.address().port}`;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tile-proxy-'));
  const file = path.join(dir, 'config.json');
  fs.writeFileSync(file, JSON.stringify({ service: origin, scenery: origin, version: 'pin' }));
  const proxy = createSharedTileProxy(file);
  await listen(proxy);
  const base = `http://127.0.0.1:${proxy.address().port}`;
  const wait = async test => { for (let i = 0; i < 100 && !test(); i++) await new Promise(r => setTimeout(r, 10)); };
  try {
    const started = Date.now();
    for (const tiles of ['t0_0_0', 't0_1_0', 't0_2_0']) {
      const res = await fetch(`${base}/coarse/demand?client=a&tiles=${tiles}`);
      assert.equal(res.status, 200); await res.json();
    }
    assert.ok(Date.now() - started < 500, 'a stalled farm never holds the demand answer');
    await wait(() => demandCalls.length === 1);
    assert.equal(demandCalls.length, 1, 'one demand in flight per client');
    hold();
    await wait(() => demandCalls.length === 2);
    // The queued demands collapsed into the newest one.
    assert.equal(demandCalls.length, 2);
    assert.equal(new URL(demandCalls[1], origin).searchParams.get('tiles'), 't0_2_0');
    await new Promise(r => setTimeout(r, 50));
    assert.deepEqual(await (await fetch(`${base}/coarse/demand?client=a&tiles=t0_0_0`)).json(), { version: 8 });
    assert.deepEqual(await (await fetch(`${base}/coarse/demand?client=b&tiles=t0_0_0`)).json(), { version: 8 });
    // Scenery: one upstream ask, the repeat from the cache; a stale answer is served and refreshed behind it.
    const q = `${base}/coarse/scenery?tiles=t2_1_-1`;
    assert.equal((await (await fetch(q)).json()).tiles[0].key, 'k1');
    assert.equal((await (await fetch(q)).json()).tiles[0].key, 'k1');
    assert.equal(sceneryCalls, 1);
  } finally {
    await close(proxy); await close(upstream); fs.rmSync(dir, { recursive: true });
  }
});

test('a farm body cut short is never passed on as a shorter complete answer', async () => {
  // city.lab 2026-11-07: a restart cut a 1,042,328-byte immutable GLB at 385,991 bytes and Chrome kept the prefix.
  const glb = Buffer.alloc(300000, 5);
  let manifestCuts = 1;
  const upstream = http.createServer((req, res) => {
    const pathname = req.url.split('?')[0];
    const cut = (body, length) => {
      res.writeHead(200, { 'content-type': pathname.endsWith('.json') ? 'application/json' : 'model/gltf-binary', 'content-length': length,
        'cache-control': 'public,max-age=31536000,immutable' });
      res.write(body, () => res.socket.destroy());
    };
    if (pathname.endsWith('/manifest.json')) {
      const text = Buffer.from(JSON.stringify({ coarse: true, stale: false, url: `/v/${'c'.repeat(40)}/coarse/t0_0_0/a.glb` }));
      if (manifestCuts-- > 0) return cut(text.subarray(0, 20), text.length);
      res.writeHead(200, { 'content-type': 'application/json', 'content-length': text.length }); return res.end(text);
    }
    cut(glb.subarray(0, 100000), glb.length);
  });
  await listen(upstream);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tile-proxy-cut-'));
  const config = path.join(root, 'config.json');
  fs.writeFileSync(config, JSON.stringify({ service: `http://127.0.0.1:${upstream.address().port}`, version: 'fixture' }));
  const proxy = createSharedTileProxy(config);
  await listen(proxy);
  const get = p => new Promise(resolve => http.get({ host: '127.0.0.1', port: proxy.address().port, path: p }, reply => {
    let bytes = 0;
    reply.on('data', c => { bytes += c.length; });
    reply.on('end', () => resolve({ status: reply.statusCode, complete: reply.complete, bytes, body: null }));
    reply.on('error', () => {});
    reply.on('close', () => { if (!reply.complete) resolve({ status: reply.statusCode, complete: false, bytes, length: Number(reply.headers['content-length']) }); });
  }));
  try {
    // A streamed payload keeps the farm's length and is reset where the farm stopped.
    const payload = await get(`/coarse/_v/${'c'.repeat(40)}/shared/a.glb`);
    assert.equal(payload.complete, false);
    assert.equal(payload.length, glb.length);
    assert.ok(payload.bytes < glb.length);
    // A collected fragment cut short is asked again instead of hanging every later request for the tile.
    const manifest = await fetch(`http://127.0.0.1:${proxy.address().port}/coarse/t0_0_0/manifest.json?d=5`);
    assert.equal(manifest.status, 200);
    assert.equal((await manifest.json()).url, `/coarse/_v/${'c'.repeat(40)}/t0_0_0/a.glb`);
  } finally {
    proxy.closeAllConnections(); upstream.closeAllConnections();
    await Promise.all([close(proxy), close(upstream)]);
    fs.rmSync(root, { recursive: true });
  }
});
