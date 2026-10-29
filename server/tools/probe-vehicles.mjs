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
let clockOffset = 0;
const timeout = setTimeout(() => { for (const s of sockets) s.ws.terminate(); process.exitCode = 1; }, 12000);
try {
  let url = process.argv[2];
  if (!url) {
    app = createServer({ store: new ScoreStore(directory), now: () => Date.now() + clockOffset });
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
  // Live probes keep their temporary registry actors below the playable city.
  const pose = { t: 1, E: a.welcome.you.spawn.E, N: a.welcome.you.spawn.N, H: app ? 4 : -200,
    yaw: 0, vE: 0, vN: 0, vH: 0, mode: 'foot', vehicle: null, carrying: false };
  a.send({ type: 'pose', ...pose }); b.send({ type: 'pose', ...pose });
  const id = `${a.welcome.you.id}:probe`;
  a.send({ type: 'vehicle', action: 'acquire', request: 'take-a', id, localId: 'probe',
    pose: { ...pose, mode: 'car', vehicle: 'player' } });
  const acquired = await a.wait(m => m.type === 'vehicle_result' && m.request === 'take-a');
  assert.equal(acquired.ok, true);
  if (app) {
    clockOffset += 65_000;
    app.room.tick();
    assert.equal(app.room.vehicles.entries.get(id).occupant, a.welcome.you.id);
    a.send({ type: 'pose', ...pose, mode: 'car', vehicle: 'player', vehicleId: id });
  }
  b.send({ type: 'vehicle', action: 'acquire', request: 'race', id });
  const raced = await b.wait(m => m.type === 'vehicle_result' && m.request === 'race');
  assert.equal(raced.ok, false); assert.equal(raced.reason, 'seat_taken');
  a.send({ type: 'vehicle', action: 'release', request: 'leave', id });
  assert.equal((await a.wait(m => m.type === 'vehicle_result' && m.request === 'leave')).ok, true);
  const c = await connect();
  assert.ok(c.welcome.vehicles.upsert.some(v => v.id === id && v.occupant === null));
  b.send({ type: 'vehicle', action: 'acquire', request: 'take-b', id });
  const transfer = await b.wait(m => m.type === 'vehicle_result' && m.request === 'take-b');
  assert.equal(transfer.ok, true); assert.equal(transfer.vehicle.owner, b.welcome.you.id);
  assert.equal(transfer.vehicle.epoch, acquired.vehicle.epoch + 1);
  const drivenPose = { ...transfer.vehicle.pose, E: Math.round((pose.E + 40) * 1000) / 1000, t: 2 };
  b.send({ type: 'pose', ...pose, mode: 'car', vehicle: 'player', vehicleId: id,
    vehicles: [{ id, epoch: transfer.vehicle.epoch, pose: drivenPose }] });
  const rejoined = await connect(b.welcome.session);
  assert.ok(rejoined.welcome.vehicles.upsert.some(v => v.id === id && v.occupant === b.welcome.you.id && v.owner === b.welcome.you.id));
  assert.equal(rejoined.welcome.vehicles.upsert.find(v => v.id === id).pose.E, drivenPose.E);
  rejoined.send({ type: 'vehicle', action: 'acquire', request: 'reclaim', id });
  const reclaimed = await rejoined.wait(m => m.type === 'vehicle_result' && m.request === 'reclaim');
  assert.equal(reclaimed.ok, true); assert.equal(reclaimed.vehicle.epoch, transfer.vehicle.epoch);
  const closed = once(rejoined.ws, 'close');
  rejoined.ws.close(); await closed;
  a.send({ type: 'vehicle', action: 'acquire', request: 'reserved', id });
  assert.equal((await a.wait(m => m.type === 'vehicle_result' && m.request === 'reserved')).reason, 'seat_taken');
  const restored = await connect(b.welcome.session);
  assert.ok(restored.welcome.vehicles.upsert.some(v => v.id === id && v.occupant === b.welcome.you.id));
  restored.send({ type: 'vehicle', action: 'release', request: 'cleanup', id });
  assert.equal((await restored.wait(m => m.type === 'vehicle_result' && m.request === 'cleanup')).ok, true);
  const personal = [];
  for (const [i, mode] of ['car', 'scooter', 'truck'].entries()) {
    const ownId = `${b.welcome.you.id}:reload-${mode}`;
    const parkedPose = { ...pose, E: Math.round((pose.E + 40) * 1000) / 1000,
      N: Math.round((pose.N + (i + 1) * 25) * 1000) / 1000, mode, vehicle: mode === 'car' ? 'player' : mode };
    restored.send({ type: 'vehicle', action: 'register', request: `park-${mode}`, id: ownId, localId: `reload-${mode}`, pose: parkedPose });
    assert.equal((await restored.wait(m => m.type === 'vehicle_result' && m.request === `park-${mode}`)).ok, true);
    personal.push({ id: ownId, pose: parkedPose });
  }
  const parkedClosed = once(restored.ws, 'close');
  restored.ws.close(); await parkedClosed;
  const parkedReload = await connect(b.welcome.session);
  for (const expected of personal) {
    const v = parkedReload.welcome.vehicles.upsert.find(v => v.id === expected.id);
    assert.ok(v); assert.equal(v.owner, b.welcome.you.id); assert.equal(v.occupant, null);
    assert.equal(v.pose.E, expected.pose.E); assert.equal(v.pose.N, expected.pose.N);
    const observed = await c.wait(m => m.type === 'vehicles' && m.upsert.some(v => v.id === expected.id));
    assert.equal(observed.upsert.find(v => v.id === expected.id).pose.E, expected.pose.E);
  }
  console.log(JSON.stringify({ ok: true, protocol: 2, sharedVehicles: true, seatRace: true, release: true, lateJoin: true,
    transfer: true, reconnect: true, socketReplacement: true, disconnectReservation: true,
    drivingReloadPosition: true, parkedReload: personal.length, observerConsistent: true, ...(app ? { stallOverLease: true } : {}) }));
} finally {
  clearTimeout(timeout);
  for (const { ws } of sockets) ws.terminate();
  if (app) await app.close();
  rmSync(directory, { recursive: true, force: true });
}
