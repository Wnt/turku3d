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
