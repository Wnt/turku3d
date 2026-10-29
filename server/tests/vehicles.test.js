import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, pose } from './helpers.js';
import { VEHICLE_LIMITS } from '../vehicles.js';
import { contactImpulse, TUNING } from '../vehicle-contact.js';
import { LIMITS } from '../room.js';

function car(client, change = {}) {
  const id = `${client.welcome.you.id}:player`;
  return { type: 'vehicle', action: 'register', request: 'test', id, localId: 'player', source: null,
    pose: { ...pose, mode: 'car', vehicle: 'player', vE: 0 }, ...change };
}
const result = c => c.messages.filter(m => m.type === 'vehicle_result').at(-1);

test('starter, parked car, scooter and truck remain after release and join in their last pose', () => {
  const f = fixture(), a = f.connect(); a.send(pose);
  for (const [index, mode] of ['car', 'car', 'scooter', 'truck'].entries()) {
    const command = car(a, { id: index === 1 ? 'park:tile:bay' : `${a.welcome.you.id}:v${index}`,
      localId: `v${index}`, source: index === 1 ? 'tile:bay' : null,
      pose: { ...pose, mode, vehicle: mode === 'car' ? index === 1 ? 'sedan/red' : 'player' : mode } });
    a.send({ ...command, action: 'acquire' }); assert.equal(result(a).ok, true);
    const vehicle = result(a).vehicle;
    a.send({ ...pose, vehicles: [{ id: vehicle.id, epoch: vehicle.epoch, pose: { ...vehicle.pose, E: 177, vE: 0 } }] });
    a.send({ type: 'vehicle', action: 'release', id: vehicle.id, request: 'leave' });
  }
  const b = f.connect();
  assert.equal(b.welcome.vehicles.upsert.length, 4);
  assert.ok(b.welcome.vehicles.upsert.every(v => !v.occupant && v.pose.E === 177));
  assert.deepEqual(b.welcome.vehicles.sources, ['tile:bay']);
});

test('parked seat race is atomic, ownership transfers and old epoch updates cannot overwrite', () => {
  const f = fixture(), a = f.connect(), b = f.connect(); a.send(pose); b.send(pose);
  const command = car(a, { id: 'park:bay', source: 'bay', action: 'acquire' });
  a.send(command); b.send(command);
  assert.equal(result(a).ok, true); assert.equal(result(b).ok, false);
  assert.equal(result(b).reason, 'seat_taken');
  assert.equal(result(b).vehicle.occupant, a.welcome.you.id);
  const old = result(a).vehicle;
  a.send({ type: 'vehicle', action: 'release', id: old.id, request: 'leave' });
  b.send({ type: 'vehicle', action: 'acquire', id: old.id, request: 'take' });
  const next = result(b).vehicle;
  assert.equal(next.owner, b.welcome.you.id); assert.equal(next.epoch, old.epoch + 1);
  a.send({ ...pose, vehicles: [{ id: old.id, epoch: old.epoch, pose: { ...old.pose, E: 800 } }] });
  b.send({ ...pose, vehicles: [{ id: old.id, epoch: old.epoch, pose: { ...old.pose, E: 900 } }] });
  assert.equal(f.room.vehicles.entries.get(old.id).pose.E, old.pose.E);
  assert.equal(f.room.stats.rejected, 2);
});

test('a connected driver keeps the seat through idle and vehicle lifetimes and can resume poses', () => {
  const f = fixture(), a = f.connect(); a.send(pose);
  a.send(car(a, { action: 'acquire' }));
  const v = result(a).vehicle;
  f.advance(LIMITS.idleMs + 1);
  assert.equal(f.room.livePlayers().length, 0);
  assert.equal(f.room.vehicles.entries.get(v.id).occupant, a.welcome.you.id);
  f.advance(VEHICLE_LIMITS.lifetime + 1);
  const b = f.connect(); b.send(pose);
  b.send({ type: 'vehicle', action: 'acquire', id: v.id, request: 'race-after-stall' });
  assert.equal(result(b).reason, 'seat_taken');
  a.send({ ...v.pose, type: 'pose', vehicleId: v.id });
  assert.equal(f.room.players.get(a.welcome.you.id).online, true);
  assert.equal(f.room.stats.rejected, 0);
  assert.equal(f.room.vehicles.entries.get(v.id).epoch, v.epoch);
});

test('driving reconnect and socket replacement retain the seat even far from the last player pose', () => {
  const f = fixture('2026-10-27T12:59:50Z'), a = f.connect(); a.send(pose);
  a.send(car(a, { action: 'acquire' }));
  const v = result(a).vehicle;
  a.send({ ...pose, vehicles: [{ id: v.id, epoch: v.epoch, pose: { ...v.pose, E: pose.E + 226 } }] });
  f.room.disconnect(a.c);
  f.advance(20_000); // Reconnect reservation survives a score-hour boundary too.
  const b = f.connect(a.welcome.session);
  assert.equal(b.welcome.you.id, a.welcome.you.id);
  assert.equal(b.welcome.vehicles.upsert.find(x => x.id === v.id).occupant, b.welcome.you.id);
  b.send({ type: 'vehicle', action: 'acquire', id: v.id, request: 'reclaim' });
  assert.equal(result(b).ok, true);
  assert.equal(result(b).vehicle.epoch, v.epoch);
  const c = f.connect(a.welcome.session);
  assert.equal(c.welcome.vehicles.upsert.find(x => x.id === v.id).occupant, c.welcome.you.id);
  assert.deepEqual(b.closes, [[4001, 'session replaced']]);
});

test('only an explicit exit or a departed holder after reconnect grace allows another driver', () => {
  const f = fixture(), a = f.connect(), b = f.connect(); a.send(pose); b.send(pose);
  a.send(car(a, { action: 'acquire' }));
  const v = result(a).vehicle;
  f.room.disconnect(a.c);
  f.advance(VEHICLE_LIMITS.reconnectMs - 1);
  b.send({ type: 'vehicle', action: 'acquire', id: v.id, request: 'too-soon' });
  assert.equal(result(b).reason, 'seat_taken');
  f.advance(1);
  b.send({ type: 'vehicle', action: 'acquire', id: v.id, request: 'departed' });
  assert.equal(result(b).ok, true);
  assert.equal(result(b).vehicle.epoch, v.epoch + 1);
});

test('interest deltas send resting cars once, remove distant replicas, keep global slot reservations', () => {
  const f = fixture(), a = f.connect(), b = f.connect(); a.send(pose); b.send({ ...pose, E: 1000 });
  a.send(car(a, { id: 'park:bay', source: 'bay' }));
  f.room.flushPoses();
  assert.deepEqual(b.messages.filter(m => m.type === 'vehicles').at(-1),
    { v: 2, type: 'vehicles', upsert: [], remove: [], sources: ['bay'] });
  b.send(pose); f.room.flushPoses();
  assert.equal(b.messages.filter(m => m.type === 'vehicles').at(-1).upsert.length, 1);
  b.messages.length = 0;
  for (let i = 0; i < 20; i++) { f.advance(50); f.room.flushPoses(); }
  assert.equal(b.messages.filter(m => m.type === 'vehicles').length, 0);
  b.send({ ...pose, E: 1000 }); f.room.flushPoses();
  assert.deepEqual(b.messages.filter(m => m.type === 'vehicles').at(-1).remove, ['park:bay']);
});

test('caps, expiry, malformed batches and disconnected owners are bounded', () => {
  const f = fixture(), a = f.connect(); a.send(pose);
  for (let i = 0; i <= VEHICLE_LIMITS.perPlayer; i++) a.send(car(a, { id: `${a.welcome.you.id}:v${i}`, localId: `v${i}` }));
  assert.equal(result(a).ok, true); assert.equal(f.room.vehicles.entries.size, VEHICLE_LIMITS.perPlayer);
  assert.equal(f.room.vehicles.entries.has(`${a.welcome.you.id}:v0`), false);
  a.send({ ...pose, vehicles: Array(9).fill(null) });
  assert.equal(f.room.stats.rejected, 1);
  f.room.disconnect(a.c);
  assert.ok([...f.room.vehicles.entries.values()].every(v => v.occupant === null && v.pose.vE === 0));
  f.advance(VEHICLE_LIMITS.lifetime);
  assert.equal(f.room.vehicles.entries.size, 0);
});

test('equal and unequal masses receive equal opposite bounded impulses; side-by-side and overpass miss', () => {
  const v = (mode, E, N, vE, H = 4) => ({ half: Object.fromEntries(['x', 'y', 'z'].map((k, i) => [k, TUNING[mode].half[i]])),
    pose: { ...pose, mode, E, N, H, yaw: -Math.PI / 2, vE, vN: 0 } });
  for (const mode of ['car', 'truck', 'scooter']) {
    const a = v('car', 0, 0, 25), b = v(mode, 5, 0, 0), impulse = contactImpulse(a, b);
    assert.ok(impulse && impulse.E > 0); assert.ok(Math.abs(impulse.N) < 1e-9);
    assert.ok(impulse.magnitude / TUNING.car.mass <= 8);
    assert.ok(impulse.magnitude / TUNING[mode].mass <= 8);
  }
  assert.equal(contactImpulse(v('car', 0, 0, 30), v('car', 5, 3, 0)), null);
  assert.equal(contactImpulse(v('car', 0, 0, 30), v('car', 5, 0, 0, 8)), null);
  assert.equal(contactImpulse(v('car', 0, 0, -30), v('car', 5, 0, 0)), null);
});

test('server emits both halves only once per cooldown, and takes over an offline empty car', () => {
  const f = fixture(), a = f.connect(), b = f.connect(); a.send(pose); b.send(pose);
  a.send(car(a, { pose: { ...pose, mode: 'car', vehicle: 'player', E: 173, N: -252, yaw: -Math.PI / 2, vE: 20 } }));
  b.send(car(b, { pose: { ...pose, mode: 'truck', vehicle: 'truck', E: 178, N: -252, yaw: -Math.PI / 2, vE: 0 } }));
  f.room.disconnect(b.c); f.room.flushPoses();
  const hits = a.messages.filter(m => m.type === 'vehicle_impulse');
  assert.equal(hits.length, 2); assert.equal(hits[0].E, -hits[1].E);
  assert.equal(f.room.vehicles.entries.get(`${b.welcome.you.id}:player`).owner, a.welcome.you.id);
  f.room.flushPoses(); assert.equal(a.messages.filter(m => m.type === 'vehicle_impulse').length, 2);
});

test('16 drivers fit 20 messages/s with 8 records each and 32 socket admission', () => {
  const f = fixture();
  const clients = Array.from({ length: 32 }, () => f.connect());
  for (const [i, c] of clients.slice(0, 16).entries()) {
    c.send({ ...pose, E: i * 40 });
    for (let j = 0; j < 8; j++) c.send(car(c, { id: `${c.welcome.you.id}:v${j}`, localId: `v${j}`,
      pose: { ...pose, E: i * 40, N: j * 10 - 252, mode: 'car', vehicle: 'player', vE: 0 } }));
  }
  f.advance(1000);
  const rejected = f.room.stats.rejected;
  for (let frame = 0; frame < 40; frame++) {
    for (const [i, c] of clients.slice(0, 16).entries()) {
      const v = f.room.vehicles.entries.get(`${c.welcome.you.id}:v0`);
      c.send({ ...pose, E: i * 40 + frame * .05, t: frame,
        vehicles: [{ id: v.id, epoch: v.epoch, pose: { ...v.pose, t: frame, E: i * 40 + frame * .05, vE: 1 } }] });
    }
    f.room.flushPoses(); f.advance(50);
  }
  assert.equal(f.room.vehicles.entries.size, 128);
  assert.equal(f.room.stats.rejected, rejected); assert.equal(rejected, 0);
  assert.equal(f.room.connections.size, 32);
});

test('contact transfers an online owner\'s empty vehicle to its hitter before sending impulses', () => {
  const f = fixture(), a = f.connect(), b = f.connect(); a.send(pose); b.send(pose);
  a.send(car(a, { action: 'acquire', pose: { ...pose, mode: 'car', vehicle: 'player', yaw: -Math.PI / 2, vE: 10 } }));
  b.send(car(b, { pose: { ...pose, mode: 'car', vehicle: 'player', E: 178, yaw: -Math.PI / 2, vE: 0 } }));
  const aid = a.welcome.you.id, bid = b.welcome.you.id;
  a.messages.length = b.messages.length = 0;
  f.room.flushPoses();
  const target = f.room.vehicles.entries.get(`${bid}:player`);
  assert.equal(target.owner, aid); assert.equal(target.epoch, 2);
  assert.equal(a.messages[0].type, 'vehicles');
  assert.equal(a.messages.filter(m => m.type === 'vehicle_impulse').length, 2);
  assert.equal(b.messages.filter(m => m.type === 'vehicle_impulse').length, 0);
});

test('missing or malformed nested vehicle poses are rejected without crashing the room', () => {
  const f = fixture(), a = f.connect();
  for (const invalid of [undefined, null, [], {}, { ...pose, mode: 'car', vehicle: 'player', rot: {} }]) a.send(car(a, { pose: invalid }));
  assert.equal(f.room.vehicles.entries.size, 0);
  assert.equal(f.room.stats.rejected, 5);
  a.send(car(a));
  const v = result(a).vehicle;
  a.send({ ...pose, vehicles: [{ id: v.id, epoch: v.epoch, pose: null }] });
  assert.equal(f.room.stats.rejected, 6);
  assert.equal(f.room.connections.size, 1);
});

test('changed replicas use shallow patches; unchanged wheels and metadata do not repeat', () => {
  const f = fixture(), a = f.connect(), b = f.connect(); a.send(pose); b.send(pose);
  a.send(car(a));
  const v = result(a).vehicle;
  a.messages.length = b.messages.length = 0;
  a.send({ ...pose, vehicles: [{ id: v.id, epoch: v.epoch, pose: { ...v.pose, t: 2, E: v.pose.E + 1 } }] });
  f.room.flushPoses();
  const delta = b.messages.find(m => m.type === 'vehicles');
  assert.deepEqual(delta.upsert, []);
  assert.deepEqual(delta.patches[0].pose, { t: 2, E: v.pose.E + 1 });
  assert.equal(a.messages.some(m => m.type === 'vehicles'), false);
});
