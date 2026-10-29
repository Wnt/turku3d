// Real WebSockets, isolated room by default. Pass a URL explicitly to probe a deployment.
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { WebSocket } from 'ws';
import { once } from 'node:events';
import { createServer } from '../http.js';
import { ScoreStore } from '../store.js';

const work = process.env.TURKU3D_WORK ?? tmpdir();
mkdirSync(work, { recursive: true });
const directory = mkdtempSync(join(work, 'mp-vehicles-'));
const sockets = [];
let app;
const timeout = setTimeout(() => { for (const s of sockets) s.ws.terminate(); process.exitCode = 1; }, 12000);
try {
  let url = process.argv[2];
  if (!url) {
    app = createServer({ store: new ScoreStore(directory) });
    app.server.listen(Number(process.env.TURKU3D_PORT_BASE ?? 5400) + 3, '127.0.0.1');
    await once(app.server, 'listening');
    url = `ws://127.0.0.1:${app.server.address().port}`;
  }
  async function connect(session) {
    const ws = new WebSocket(url, { origin: 'https://wnt.github.io' });
    const messages = [], waiters = new Set();
    const peer = { ws, messages, send: fields => ws.send(JSON.stringify({ v: 2, ...fields })),
      wait: predicate => new Promise((resolve, reject) => {
        const old = messages.find(predicate);
        if (old) { resolve(old); return; }
        const timer = setTimeout(() => { waiters.delete(check); reject(Error('Vehicle probe message timeout')); }, 4000);
        const check = m => { if (predicate(m)) { clearTimeout(timer); waiters.delete(check); resolve(m); } };
        waiters.add(check);
      }) };
    sockets.push(peer);
    ws.on('message', data => { const m = JSON.parse(data); messages.push(m); for (const check of waiters) check(m); });
    ws.on('error', () => {});
    await new Promise((resolve, reject) => { ws.once('open', resolve); ws.once('error', reject); });
    peer.send({ type: 'hello', ...(session ? { session } : {}) });
    peer.welcome = await peer.wait(m => m.type === 'welcome');
    return peer;
  }
  const a = await connect(), b = await connect();
  const pose = { t: 1, E: a.welcome.you.spawn.E, N: a.welcome.you.spawn.N, H: 4,
    yaw: 0, vE: 0, vN: 0, vH: 0, mode: 'foot', vehicle: null, carrying: false };
  a.send({ type: 'pose', ...pose }); b.send({ type: 'pose', ...pose });
  const id = `${a.welcome.you.id}:probe`;
  a.send({ type: 'vehicle', action: 'acquire', request: 'take-a', id, localId: 'probe',
    pose: { ...pose, mode: 'car', vehicle: 'player' } });
  const acquired = await a.wait(m => m.type === 'vehicle_result' && m.request === 'take-a');
  assert.equal(acquired.ok, true);
  b.send({ type: 'vehicle', action: 'acquire', request: 'race', id });
  assert.equal((await b.wait(m => m.type === 'vehicle_result' && m.request === 'race')).ok, false);
  a.send({ type: 'vehicle', action: 'release', request: 'leave', id });
  assert.equal((await a.wait(m => m.type === 'vehicle_result' && m.request === 'leave')).ok, true);
  const c = await connect();
  assert.ok(c.welcome.vehicles.upsert.some(v => v.id === id && v.occupant === null));
  b.send({ type: 'vehicle', action: 'acquire', request: 'take-b', id });
  const transfer = await b.wait(m => m.type === 'vehicle_result' && m.request === 'take-b');
  assert.equal(transfer.ok, true); assert.equal(transfer.vehicle.owner, b.welcome.you.id);
  assert.equal(transfer.vehicle.epoch, acquired.vehicle.epoch + 1);
  const rejoined = await connect(b.welcome.session);
  assert.ok(rejoined.welcome.vehicles.upsert.some(v => v.id === id && !v.occupant && v.owner === b.welcome.you.id));
  console.log(JSON.stringify({ ok: true, protocol: 2, sharedVehicles: true, seatRace: true, release: true, lateJoin: true, transfer: true, reconnect: true }));
} finally {
  clearTimeout(timeout);
  for (const { ws } of sockets) ws.terminate();
  if (app) await app.close();
  rmSync(directory, { recursive: true, force: true });
}
