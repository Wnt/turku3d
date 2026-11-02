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
    const batch = { batch: 0, meta: { name: 'test' }, frames: [[1, 2, 3, 4, 5, 6, 7]], poses: [{ t: 1 }], marks: [{ k: 'pause', t: 2 }] };
    const post = headers => fetch(`${base}/__capture/test`, { method: 'POST', headers, body: JSON.stringify(batch) });
    assert.equal((await post()).status, 200); assert.equal((await post()).status, 200);
    assert.deepEqual(JSON.parse(await fs.readFile(path.join(dir, 'test/frames.jsonl'), 'utf8')), batch.frames);
    assert.equal((await fs.readFile(path.join(dir, 'test/poses.jsonl'), 'utf8')).trim(), '{"t":1}');
    assert.equal((await post({ Origin: 'http://elsewhere' })).status, 403);
    assert.equal((await fetch(`${base}/__capture/bad%2fpath/meta.json`)).status, 400);
    assert.equal((await fetch(`${base}/__capture/test/meta.json`)).status, 200);
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await fs.rm(dir, { recursive: true }); }
});
