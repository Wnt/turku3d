// Deployed score-contract probe: earnings survive fines and session replacement.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { WebSocket } from 'ws';

const url = process.argv[2] ?? 'wss://turku.lab.madekivi.fi/';
const sockets = [];
const deadline = setTimeout(() => { console.error('Earned probe timed out'); process.exit(1); }, 25000);
function wait(ws, type, predicate = () => true) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { ws.off('message', message); reject(new Error(`Missing ${type}`)); }, 5000);
    function message(data) {
      const m = JSON.parse(data);
      if (m.type === type && predicate(m)) { clearTimeout(timer); ws.off('message', message); resolve(m); }
    }
    ws.on('message', message);
  });
}
async function join(session) {
  const ws = new WebSocket(url, { origin: 'https://wnt.github.io', handshakeTimeout: 5000 });
  sockets.push(ws);
  const welcome = wait(ws, 'welcome');
  ws.on('error', () => {});
  await new Promise((resolve, reject) => { ws.once('open', resolve); ws.once('error', reject); });
  ws.send(JSON.stringify({ v: 2, type: 'hello', ...(session ? { session } : {}) }));
  return { ws, welcome: await welcome };
}
async function send(ws, message) {
  await new Promise(resolve => setTimeout(resolve, 100));
  ws.send(JSON.stringify({ v: 2, ...message }));
}
try {
  const { ws, welcome } = await join();
  const nick = welcome.you.nick;
  assert.equal(welcome.scores.find(s => s.nick === nick).earned, 0);
  const paid = wait(ws, 'scores', m => m.scores.some(s => s.nick === nick && s.earned === 14));
  await send(ws, { type: 'delivery', orderId: randomUUID(), addressId: 'earned-probe', payout: 6, tip: 8, routeMeters: 120 });
  const before = (await paid).scores.find(s => s.nick === nick);
  const poleId = `earned-probe:${randomUUID()}`;
  const broken = wait(ws, 'world', m => m.poleId === poleId);
  await send(ws, { type: 'world', action: 'break', poleId, by: welcome.you.id, t: 1 });
  await broken;
  const fined = wait(ws, 'scores');
  await send(ws, { type: 'fine', reason: 'pole', poleId, amount: 50 });
  assert.deepEqual((await fined).scores.find(s => s.nick === nick), before);
  const rejoin = await join(welcome.session);
  assert.equal(rejoin.welcome.you.id, welcome.you.id);
  assert.deepEqual(rejoin.welcome.scores.find(s => s.nick === nick), before);
  const base = url.replace(/^ws/, 'http').replace(/\/$/, '');
  const live = await (await fetch(`${base}/live.json`, { signal: AbortSignal.timeout(5000) })).json();
  assert.equal(live.scores.find(s => s.nick === nick).earned, 14);
  const hours = await (await fetch(`${base}/scores/index.json`, { signal: AbortSignal.timeout(5000) })).json();
  if (hours.length) {
    const archive = await (await fetch(`${base}/scores/${encodeURIComponent(hours[0].id)}.json`, { signal: AbortSignal.timeout(5000) })).json();
    assert.ok(archive.players.every(s => Number.isInteger(s.earned ?? s.money)));
  }
  console.log(JSON.stringify({ ok: true, earned: 14, fineExcluded: true, reconnect: true, http: true, archives: hours.length }));
} finally {
  clearTimeout(deadline);
  for (const ws of sockets) ws.close();
}
