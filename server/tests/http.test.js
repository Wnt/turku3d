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

async function start(t, options = {}) {
  const directory = mkdtempSync(join(tmpdir(), 'turku-mp-http-'));
  const store = new ScoreStore(directory);
  const app = createServer({ store, ...options });
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

function messageOf(ws, type, accept = () => true) {
  return new Promise(resolve => {
    const listener = data => {
      const message = JSON.parse(data);
      if (message.type === type && accept(message)) { ws.off('message', listener); resolve(message); }
    };
    ws.on('message', listener);
  });
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
  const share = ['https://city.lab.madekivi.fi'];
  assert.equal(allowedOrigin('https://city.lab.madekivi.fi', share), true);
  assert.equal(allowedOrigin('https://wnt.github.io', share), true);
  for (const origin of [undefined, 'https://city.lab.madekivi.fi/', 'http://city.lab.madekivi.fi', 'https://x.city.lab.madekivi.fi']) {
    assert.equal(allowedOrigin(origin, share), false, String(origin));
  }
});

test('a share server accepts its own page origin for sockets and scores, and no other', async t => {
  const app = await start(t, { origins: ['https://city.lab.madekivi.fi'] });
  const ws = await socket(`${app.ws}/`, 'https://city.lab.madekivi.fi');
  ws.terminate();
  assert.equal(await denied(`${app.ws}/`, 'https://evil.lab.madekivi.fi'), 403);
  const live = await fetch(`${app.http}/live.json`, { headers: { origin: 'https://city.lab.madekivi.fi' } });
  assert.equal(live.status, 200);
  assert.equal(live.headers.get('access-control-allow-origin'), 'https://city.lab.madekivi.fi');
  assert.equal((await fetch(`${app.http}/live.json`, { headers: { origin: 'https://evil.lab.madekivi.fi' } })).status, 403);
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
  const welcome = once(ws, 'message'); ws.send(JSON.stringify({ v: 2, type: 'hello' }));
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
  assert.deepEqual(JSON.parse((await message)[0]), { v: 2, type: 'error', code: 'protocol', reload: true });
  assert.equal((await closed)[0], 1002);
  const big = await socket(app.ws);
  const end = once(big, 'close'); big.send('x'.repeat(65_537));
  assert.equal((await end)[0], 1009);
});

test('ephemeral WebSocket server shares pole breaks and fines', async t => {
  const app = await start(t);
  const a = await socket(app.ws), b = await socket(app.ws);
  const welcomeA = messageOf(a, 'welcome'); a.send(JSON.stringify({ v: 2, type: 'hello' }));
  const own = await welcomeA;
  const welcomeB = messageOf(b, 'welcome'); b.send(JSON.stringify({ v: 2, type: 'hello' }));
  await welcomeB;
  const world = messageOf(b, 'world'), scores = messageOf(b, 'scores', m => m.scores.some(s => s.nick === own.you.nick && s.earned === 0));
  const poleId = 't0_0_0:GIS_Liikennemerkit:123';
  a.send(JSON.stringify({ v: 2, type: 'world', action: 'break', poleId, by: own.you.id, t: 1 }));
  a.send(JSON.stringify({ v: 2, type: 'fine', reason: 'pole', poleId, amount: 50 }));
  assert.equal((await world).poleId, poleId);
  assert.equal((await scores).scores.find(s => s.nick === own.you.nick).earned, 0);
  const c = await socket(app.ws), welcomeC = messageOf(c, 'welcome');
  c.send(JSON.stringify({ v: 2, type: 'hello' }));
  assert.deepEqual((await welcomeC).brokenPoles.map(p => p.poleId), [poleId]);
});

test('each city is its own room: players in Turku and Espoo never see each other, scores stay apart', async t => {
  const app = await start(t);
  const turku = await socket(`${app.ws}/`);
  const turkuWelcome = messageOf(turku, 'welcome');
  turku.send(JSON.stringify({ v: 2, type: 'hello' }));
  const espoo = await socket(`${app.ws}/city/espoo`);
  const espooWelcome = messageOf(espoo, 'welcome');
  espoo.send(JSON.stringify({ v: 2, type: 'hello' }));
  const [a, b] = await Promise.all([turkuWelcome, espooWelcome]);
  assert.deepEqual(a.players.map(p => p.id), [a.you.id]);
  assert.deepEqual(b.players.map(p => p.id), [b.you.id]);
  // A Turku session is unknown in the Espoo room: it gets its own player there.
  const again = await socket(`${app.ws}/city/espoo`);
  const third = messageOf(again, 'welcome');
  again.send(JSON.stringify({ v: 2, type: 'hello', session: a.session }));
  assert.notEqual((await third).you.id, a.you.id);
  const live = await (await fetch(`${app.http}/live.json?city=espoo`)).json();
  assert.equal(live.players.length, 2);
  assert.equal((await (await fetch(`${app.http}/live.json`)).json()).players.length, 1);
  assert.equal((await fetch(`${app.http}/live.json?city=narnia`)).status, 404);
  assert.deepEqual((await (await fetch(`${app.http}/healthz`)).json()).cities.espoo, { connections: 2, online: 2 });
  for (const path of ['/city/turku', '/city/narnia', '/city/', '/city/espoo/x']) assert.equal(await denied(`${app.ws}${path}`, 'https://wnt.github.io'), 404, path);
  for (const ws of [turku, espoo, again]) ws.terminate();
});

test('a move carries the socket to another city room: the old room sees the player go, the new one sees it arrive', async t => {
  const app = await start(t);
  const watcher = await socket(`${app.ws}/`);
  const watching = messageOf(watcher, 'welcome');
  watcher.send(JSON.stringify({ v: 2, type: 'hello' }));
  await watching;
  const espooWatcher = await socket(`${app.ws}/city/espoo`);
  const espooWatching = messageOf(espooWatcher, 'welcome');
  espooWatcher.send(JSON.stringify({ v: 2, type: 'hello' }));
  await espooWatching;
  const traveller = await socket(`${app.ws}/`);
  const home = messageOf(traveller, 'welcome');
  traveller.send(JSON.stringify({ v: 2, type: 'hello' }));
  const turkuSelf = await home;
  assert.equal((await (await fetch(`${app.http}/live.json`)).json()).players.length, 2);
  const arrived = messageOf(traveller, 'welcome');
  traveller.send(JSON.stringify({ v: 2, type: 'move', room: '/city/espoo' }));
  traveller.send(JSON.stringify({ v: 2, type: 'hello' }));
  const espooSelf = await arrived;
  assert.notEqual(espooSelf.you.id, turkuSelf.you.id);
  assert.deepEqual(espooSelf.players.map(p => p.id).sort(), [espooSelf.you.id, ...espooSelf.players.filter(p => p.id !== espooSelf.you.id).map(p => p.id)].sort());
  assert.equal(espooSelf.players.length, 2);
  const live = await (await fetch(`${app.http}/live.json`)).json();
  assert.equal(live.players.filter(p => p.online !== false).length, 1);
  assert.equal((await (await fetch(`${app.http}/live.json?city=espoo`)).json()).players.length, 2);
  // Back home with the Turku session: the same Turku player again.
  const back = messageOf(traveller, 'welcome');
  traveller.send(JSON.stringify({ v: 2, type: 'move', room: '/' }));
  traveller.send(JSON.stringify({ v: 2, type: 'hello', session: turkuSelf.session }));
  assert.equal((await back).you.id, turkuSelf.you.id);
  const closed = new Promise(resolve => traveller.once('close', code => resolve(code)));
  traveller.send(JSON.stringify({ v: 2, type: 'move', room: '/city/narnia' }));
  assert.equal(await closed, 1008);
  for (const ws of [watcher, espooWatcher]) ws.terminate();
});
