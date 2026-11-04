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
  const base = 'a'.repeat(64), other = 'f'.repeat(64);
  const aliases = { pin: 'p', train1: 't1', train2: 't2', 'train0-probe': 'x', train9: 't9', lidarwires: 'l' };
  // finished[version] = tiles with a ready.json; train9 sits on another (structural) generation.
  const finished = { pin: ['t0_0_0', 't0_1_0', 't0_2_0'], train1: ['t0_0_0', 't0_1_0'], train2: ['t0_0_0'], train9: ['t0_0_0', 't0_1_0', 't0_2_0'],
    'train0-probe': ['t0_2_0'] };
  const upstream = http.createServer((req, res) => {
    calls.push(`${req.method} ${req.url}`);
    res.setHeader('content-type', 'application/json');
    if (req.url === '/status') { res.end(JSON.stringify({ aliases })); return; }
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
    assert.ok(calls.filter(c => !c.endsWith('ready.json') && c !== 'GET /status').every(c => /^GET \/v\/(pin|train1|train2)\/coarse\/t0_\d_0\/manifest\.json\?d=0$/.test(c)));
    write({});  // not excluded: a train on another base generation is still never mixed in
    assert.equal(await served('t0_1_0'), 'train1');
    calls.length = 0;
    await fetch(`${origin}/coarse/demand?client=play&tiles=t0_0_0`);
    await fetch(`${origin}/coarse/navigation.json`);
    assert.deepEqual(calls, ['GET /v/pin/coarse/demand?client=play&tiles=t0_0_0', 'GET /v/pin/coarse/navigation.json']);
    const fragment = await (await fetch(`${origin}/coarse/t0_0_0/collision.json?d=0`)).json();
    assert.equal(fragment.url, `/coarse/_v/${'b'.repeat(64)}/t/mesh.glb`);
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
