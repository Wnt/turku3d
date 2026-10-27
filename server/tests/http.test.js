import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { WebSocket } from 'ws';
import { createServer, allowedOrigin } from '../http.js';
import { ScoreStore } from '../store.js';
import { hourAt } from '../hours.js';

async function start(t) {
  const directory = mkdtempSync(join(tmpdir(), 'turku-mp-http-'));
  const store = new ScoreStore(directory);
  const app = createServer({ store });
  app.server.listen(0, '127.0.0.1');
  await once(app.server, 'listening');
  t.after(async () => { await app.close(); rmSync(directory, { recursive: true, force: true }); });
  const address = `127.0.0.1:${app.server.address().port}`;
  return { ...app, store, http: `http://${address}`, ws: `ws://${address}` };
}

async function socket(url, origin = 'https://wnt.github.io') {
  const ws = new WebSocket(url, { origin });
  await once(ws, 'open');
  return ws;
}

async function denied(url, origin) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url, origin === undefined ? {} : { origin });
    ws.on('error', () => {});
    ws.on('unexpected-response', (_req, res) => { res.resume(); ws.terminate(); resolve(res.statusCode); });
    ws.on('open', () => { ws.terminate(); reject(new Error('Unexpected accepted upgrade')); });
  });
}

test('origin allowlist matches exact Pages and localhost origins', () => {
  for (const origin of ['https://wnt.github.io', 'http://localhost', 'http://localhost:5170', 'http://localhost:65535']) {
    assert.equal(allowedOrigin(origin), true, origin);
  }
  for (const origin of [undefined, 'null', 'https://evil.test', 'https://wnt.github.io.evil.test',
    'https://wnt.github.io/', 'http://localhost.evil.test:80', 'http://localhost:0',
    'http://localhost:65536', 'http://user@localhost:80', 'http://127.0.0.1:5170']) {
    assert.equal(allowedOrigin(origin), false, String(origin));
  }
});

test('HTTP serves health, live scores, archive index/files and CORS without exposing sessions', async t => {
  const app = await start(t);
  const hour = hourAt(Date.now() - 3_600_000);
  app.store.write(hour, [{ nick: 'AB', money: 6, deliveries: 1, rank: 1 }]);
  const health = await fetch(`${app.http}/healthz`);
  assert.equal(health.status, 200); assert.equal((await health.json()).ok, true);
  const live = await fetch(`${app.http}/live.json`, { headers: { Origin: 'http://localhost:5170' } });
  assert.equal(live.headers.get('access-control-allow-origin'), 'http://localhost:5170');
  assert.deepEqual((await live.json()).scores, []);
  assert.deepEqual(await (await fetch(`${app.http}/scores/index.json`)).json(), [hour]);
  const saved = await (await fetch(`${app.http}/scores/${hour.id}.json`)).json();
  assert.equal(saved.players[0].money, 6);
  assert.equal((await fetch(`${app.http}/scores/no.json`)).status, 404);
  assert.equal((await fetch(`${app.http}/scores/%ZZ.json`)).status, 400);
  assert.equal((await fetch(`${app.http}/healthz`, { headers: { Origin: 'https://evil.test' } })).status, 403);
  assert.equal((await fetch(`${app.http}/healthz`, { method: 'POST' })).status, 405);
  assert.equal((await fetch(`${app.http}/live.json`, { method: 'OPTIONS', headers: { Origin: 'https://wnt.github.io' } })).status, 204);
  const ws = await socket(app.ws);
  const welcome = once(ws, 'message'); ws.send(JSON.stringify({ v: 1, type: 'hello' }));
  const response = JSON.parse((await welcome)[0]);
  assert.equal(response.type, 'welcome'); assert.match(response.session, /^[a-f0-9]{64}$/);
  const current = await (await fetch(`${app.http}/live.json`)).text();
  assert.ok(!current.includes(response.session));
  assert.equal(JSON.parse(current).players[0].id, response.you.id);
});

test('WebSocket rejects missing/hostile Origin, wrong path, and connection 33', async t => {
  const app = await start(t);
  assert.equal(await denied(app.ws), 403);
  assert.equal(await denied(app.ws, 'https://evil.test'), 403);
  assert.equal(await denied(`${app.ws}/wrong`, 'https://wnt.github.io'), 404);
  for (let i = 0; i < 32; i++) await socket(app.ws);
  assert.equal(await denied(app.ws, 'https://wnt.github.io'), 503);
  assert.equal(app.room.connections.size, 32);
});

test('wire protocol mismatch sends reload error before close, and oversize frames close with 1009', async t => {
  const app = await start(t);
  const ws = await socket(app.ws);
  const message = once(ws, 'message'), closed = once(ws, 'close');
  ws.send(JSON.stringify({ type: 'hello', v: 999 }));
  assert.deepEqual(JSON.parse((await message)[0]), { v: 1, type: 'error', code: 'protocol', reload: true });
  assert.equal((await closed)[0], 1002);
  const big = await socket(app.ws);
  const end = once(big, 'close'); big.send('x'.repeat(65_537));
  assert.equal((await end)[0], 1009);
});
