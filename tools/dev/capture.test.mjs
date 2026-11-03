import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { captureMiddleware } from './capture.mjs';
test('capture batches preserve reference files, retry idempotently, reject traversal and foreign origins', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'capture-test-'));
  const middleware = captureMiddleware(dir);
  const server = http.createServer((req, res) => middleware(req, res, () => { res.writeHead(404); res.end(); }));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const batch = { batch: 0, meta: { name: 'test' }, frames: [[1, 2, 3, 4, 5, 6, 7]], poses: [{ t: 1 }], marks: [{ k: 'pause', t: 2 }],
      tiles: [{ t: 3, k: 'tile.full', id: 't0_0_0' }], slices: [[4, 9, 0, 1.5, 0, 0, 0, 0]] };
    const post = headers => fetch(`${base}/__capture/test`, { method: 'POST', headers, body: JSON.stringify(batch) });
    assert.equal((await post()).status, 200); assert.equal((await post()).status, 200);
    assert.deepEqual(JSON.parse(await fs.readFile(path.join(dir, 'test/frames.jsonl'), 'utf8')), batch.frames);
    assert.equal((await fs.readFile(path.join(dir, 'test/poses.jsonl'), 'utf8')).trim(), '{"t":1}');
    // Streaming trace rows (game/src/telemetry/streamTrace.ts): one event or frame slice per line.
    assert.equal((await fs.readFile(path.join(dir, 'test/tiles.jsonl'), 'utf8')).trim(), '{"t":3,"k":"tile.full","id":"t0_0_0"}');
    assert.equal((await fs.readFile(path.join(dir, 'test/slices.jsonl'), 'utf8')).trim(), '[4,9,0,1.5,0,0,0,0]');
    assert.equal((await fetch(`${base}/__capture/test/tiles.jsonl`)).status, 200);
    assert.equal((await post({ Origin: 'http://elsewhere' })).status, 403);
    assert.equal((await fetch(`${base}/__capture/bad%2fpath/meta.json`)).status, 400);
    assert.equal((await fetch(`${base}/__capture/test/meta.json`)).status, 200);
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await fs.rm(dir, { recursive: true }); }
});

for (const trigger of ['read', 'later batch']) test(`partial appends recover on ${trigger} after middleware restart, without losing prior or later rows`, async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'capture-recovery-'));
  let server, base;
  const start = async () => {
    const middleware = captureMiddleware(root);
    server = http.createServer((req, res) => middleware(req, res, () => { res.writeHead(404); res.end(); }));
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    base = `http://127.0.0.1:${server.address().port}`;
  };
  const close = async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); };
  const post = batch => fetch(`${base}/__capture/recover`, { method: 'POST', body: JSON.stringify(batch) });
  const batches = [0, 1, 2].map(n => ({ batch: n, poses: [{ t: n }], frames: [[n, n, 1, 2, n, 10, 20]], ...(n < 2 ? { meta: { version: n } } : {}) }));
  const append = fs.appendFile;
  let interrupted = false;
  t.mock.method(fs, 'appendFile', async (file, data, ...options) => {
    if (!interrupted && path.basename(file) === 'poses.jsonl' && data.includes('"t":1')) {
      interrupted = true; await append(file, data.slice(0, 4), ...options); throw Error('simulated interrupted disk write');
    }
    return append(file, data, ...options);
  });
  try {
    await start(); assert.equal((await post(batches[0])).status, 200);
    assert.equal((await post(batches[1])).status, 500);
    const directory = path.join(root, 'recover');
    assert.equal((await fs.readFile(path.join(directory, 'poses.jsonl'), 'utf8')).endsWith('\n'), false);
    await close(); await start();
    if (trigger === 'read') assert.equal((await fetch(`${base}/__capture/recover/poses.jsonl`)).status, 200);
    assert.equal((await post(batches[2])).status, 200);
    // Retrying the interrupted batch after a later successful one must preserve that later data.
    assert.equal((await post(batches[1])).status, 200);
    const read = async file => (await fs.readFile(path.join(directory, file), 'utf8')).trim().split('\n').map(JSON.parse);
    assert.deepEqual(await read('poses.jsonl'), batches.flatMap(batch => batch.poses));
    assert.deepEqual(await read('frames.jsonl'), batches.map(batch => batch.frames));
    assert.deepEqual(JSON.parse(await fs.readFile(path.join(directory, 'meta.json'), 'utf8')), { version: 1 });
    assert.equal((await fs.readdir(directory)).some(name => name.endsWith('.pending')), false);
  } finally { await close(); await fs.rm(root, { recursive: true, force: true }); }
});
