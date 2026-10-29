import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, pose, delivery } from './helpers.js';
import { nickname, NICKS, validNickname } from '../nicks.js';
import { SPAWNS } from '../room.js';

test('nicknames exhaust without collisions; reroll changes nickname', () => {
  for (const invalid of ['', 'AB', 'sneakyPizza', 'Sneaky Pizza', 'SneakyPizza1', 'SparklyHedgehogTooLong', null]) {
    assert.equal(validNickname(invalid), false);
  }
  assert.ok(NICKS.length > 3600); // More than one fresh join per second for an hour.
  assert.equal(new Set(NICKS).size, NICKS.length);
  const used = new Set();
  for (let i = 0; i < NICKS.length; i++) {
    const n = nickname(used); assert.ok(validNickname(n)); assert.ok(!used.has(n));
    used.add(n);
  }
  assert.equal(nickname(used), null);
  for (const old of NICKS) assert.notEqual(nickname(new Set(), old), old);
});

test('joins use sixteen fixed spawns in round-robin order, with 32 connections maximum', () => {
  const f = fixture();
  assert.equal(SPAWNS.length, 16);
  assert.equal(new Set(SPAWNS.map(p => `${p.E},${p.N}`)).size, 16);
  for (let i = 0; i < 32; i++) assert.deepEqual(f.connect().welcome.you.spawn, SPAWNS[i % 16]);
  assert.equal(f.room.connect(() => {}, () => {}), null);
});

test('session rejoin preserves identity, score and deduplication; concurrent rejoin replaces socket', () => {
  const f = fixture(), a = f.connect();
  a.send(delivery);
  f.room.disconnect(a.c);
  const b = f.connect(a.welcome.session);
  assert.deepEqual(b.welcome.you, a.welcome.you);
  b.send(delivery);
  assert.equal(f.room.scores()[0].earned, 14);
  assert.equal(f.room.stats.rejected, 1);
  const c = f.connect(a.welcome.session);
  assert.deepEqual(b.closes, [[4001, 'session replaced']]);
  assert.equal(f.room.connections.size, 1);
  assert.equal(c.welcome.session, a.welcome.session);
});

test('poses coalesce to latest per player and exclude their sender', () => {
  const f = fixture(), a = f.connect(), b = f.connect();
  a.messages.length = b.messages.length = 0;
  a.send(pose); a.send({ ...pose, E: 180, t: 2 }); b.send({ ...pose, E: 190 });
  assert.equal(b.messages.length, 0);
  f.room.flushPoses();
  assert.equal(a.messages[0].poses.length, 1);
  assert.equal(a.messages[0].poses[0].E, 190);
  assert.equal(b.messages[0].poses[0].E, 180);
  f.room.flushPoses();
  assert.equal(b.messages.length, 1);
});

test('pose relays vehicle identity and rejects inconsistent mode or model keys', () => {
  const f = fixture(), a = f.connect(), b = f.connect();
  a.messages.length = b.messages.length = 0;
  a.send({ ...pose, mode: 'car', vehicle: 'hatchback-sports/red' });
  f.room.flushPoses();
  assert.equal(b.messages[0].poses[0].vehicle, 'hatchback-sports/red');
  const accepted = f.room.stats.poses;
  a.send({ ...pose, mode: 'scooter', vehicle: 'hatchback-sports/red' });
  a.send({ ...pose, mode: 'truck', vehicle: 'scooter' });
  a.send({ ...pose, mode: 'car', vehicle: '../red' });
  assert.equal(f.room.stats.poses, accepted);
});

test('every vehicle mode is a valid pose with its own vehicle key; an unknown mode is rejected', () => {
  const f = fixture(), a = f.connect();
  const modes = [['foot', null], ['car', 'player'], ['scooter', 'scooter'], ['truck', 'truck']];
  for (const [t, [mode, vehicle]] of modes.entries()) a.send({ ...pose, t: t + 1, mode, vehicle });
  assert.equal(f.room.stats.poses, 4); assert.equal(f.room.stats.rejected, 0);
  a.send({ ...pose, t: 9, mode: 'tank', vehicle: 'tank' });
  assert.equal(f.room.stats.poses, 4); assert.equal(f.room.stats.rejected, 1);
});

test('rate budget accepts 20, drops excess and disconnects above 25 arrivals per second', () => {
  const f = fixture(), a = f.connect();
  f.advance(1000);
  for (let i = 0; i < 25; i++) a.send(pose);
  assert.equal(f.room.stats.poses, 20); assert.equal(f.room.stats.rejected, 5);
  assert.equal(a.closes.length, 0);
  a.send(pose);
  assert.equal(a.closes[0][0], 1008); assert.equal(f.room.stats.rejected, 6);
  const b = f.connect(); f.advance(1000);
  for (let i = 0; i < 40; i++) { b.send(pose); f.advance(50); }
  assert.equal(b.closes.length, 0);
});

test('idle removal after 60 seconds preserves scores and a pose restores presence', () => {
  const f = fixture(), a = f.connect(); a.send(delivery);
  f.advance(59_999); assert.equal(f.room.livePlayers().length, 1);
  f.advance(1); assert.equal(f.room.livePlayers().length, 0);
  assert.equal(f.room.scores()[0].earned, 14); assert.equal(f.room.scores()[0].online, false);
  a.send(pose); assert.equal(f.room.livePlayers().length, 1);
});

test('deliveries accept bounded whole euros, reject duplicates and malformed claims', () => {
  const f = fixture(), a = f.connect(), b = f.connect();
  a.send(delivery); b.send(delivery);
  assert.deepEqual(f.room.scores().map(s => s.rank), [1, 2]);
  a.send(delivery);
  for (const change of [{ payout: 50 }, { tip: -1 }, { tip: 9 }, { routeMeters: -1 },
    { routeMeters: '100' }, { payout: 10, tip: 0 }, { addressId: '' }, { orderId: '../bad' }]) {
    a.send({ ...delivery, orderId: 'another', ...change });
  }
  assert.equal(f.room.stats.rejected, 9);
  a.send({ ...delivery, orderId: 'bonus', payout: 10, tip: 1 });
  assert.equal(f.room.scores()[0].earned, 25);
  assert.deepEqual(f.room.scores().map(s => s.rank), [1, 2]);
  assert.equal(a.messages.at(-1).type, 'scores');
  a.send({ ...delivery, orderId: 'long-job', job: 'long', routeMeters: 3000, payout: 60, tip: 8 });
  assert.equal(f.room.scores()[0].earned, 93);
});

test('protocol mismatch requests reload; malformed, binary, oversized and unknown messages count as rejected', () => {
  const f = fixture(), a = f.connect();
  for (const data of ['{', '[]', 'null', JSON.stringify({ v: 2, type: 'unknown' })]) f.room.receive(a.c, data);
  f.room.receive(a.c, '{}', true);
  f.room.receive(a.c, ' '.repeat(65_537));
  a.send({ ...pose, E: 23459668 });
  assert.equal(f.room.stats.rejected, 7);
  a.send({ v: 1, type: 'pose' });
  assert.deepEqual(a.messages.at(-1), { v: 2, type: 'error', code: 'protocol', reload: true });
  assert.equal(a.closes[0][0], 1002);
});

test('uninitialized connections time out and cannot send poses', () => {
  const f = fixture(), closes = [];
  const c = f.room.connect(() => {}, (...args) => closes.push(args));
  f.room.receive(c, JSON.stringify({ v: 2, ...pose }));
  assert.equal(f.room.stats.rejected, 1);
  f.advance(5000); assert.equal(closes[0][0], 1008);
});

test('20 Hz traffic tolerates alternating 49/51 ms packet jitter without rejection', () => {
  const f = fixture(), a = f.connect(); f.advance(1000);
  for (let i = 0; i < 200; i++) {
    a.send(pose); f.advance(i % 2 ? 51 : 49);
  }
  assert.equal(f.room.stats.poses, 200);
  assert.equal(f.room.stats.rejected, 0);
  assert.equal(a.closes.length, 0);
});

test('vehicle quaternion and wheel state relay unchanged; malformed transforms are rejected', () => {
  const f = fixture(), a = f.connect(), b = f.connect();
  b.messages.length = 0;
  const rot = { x: 0.6, y: 0, z: 0, w: 0.8 };
  const wheels = [{ pos: { x: 1, y: -0.3, z: 1 }, steer: 0.2, rotation: 12, contact: true, suspension: 0.1,
    tyre: { point: { x: 1, y: -0.7, z: 1 }, normal: { x: 0, y: 1, z: 0 }, material: 1, lateral: 5, longitudinal: 3 } }];
  const car = { ...pose, mode: 'car', vehicle: 'player', rot, wheels };
  a.send(car); f.room.flushPoses();
  assert.deepEqual(b.messages[0].poses[0].rot, rot);
  assert.deepEqual(b.messages[0].poses[0].wheels, wheels);
  const accepted = f.room.stats.poses;
  for (const invalid of [{ rot: null }, { rot: { ...rot, w: 0 } }, { rot: { ...rot, x: Infinity } },
    { wheels: [null] }, { wheels: Array(5).fill(wheels[0]) }, { wheels: [{ ...wheels[0], steer: Infinity }] }]) a.send({ ...car, ...invalid });
  assert.equal(f.room.stats.poses, accepted);
});

test('earned ranking breaks ties by deliveries then earliest result and ignores fine deductions', () => {
  const f = fixture(), a = f.connect(), b = f.connect();
  a.send({ ...delivery, tip: 6 }); // 12 earned in one delivery
  f.advance(100);
  b.send({ ...delivery, tip: 0 });
  b.send({ ...delivery, orderId: 'second', tip: 0 }); // same earned, more deliveries
  assert.deepEqual(f.room.scores().map(s => [s.nick, s.earned, s.deliveries, s.rank]),
    [[b.welcome.you.nick, 12, 2, 1], [a.welcome.you.nick, 12, 1, 2]]);
  f.advance(100);
  a.send({ ...delivery, orderId: 'second', tip: 0 }); // 18 / 2 first
  f.advance(100);
  b.send({ ...delivery, orderId: 'third', tip: 0 }); // 18 / 3
  f.advance(100);
  a.send({ ...delivery, orderId: 'third', payout: 0 }); // invalid: cannot inflate deliveries
  assert.equal(f.room.scores()[0].nick, b.welcome.you.nick);
  b.send({ type: 'world', action: 'break', poleId: 'test-pole', by: b.welcome.you.id, t: 1 });
  const before = f.room.scores();
  b.send({ type: 'fine', reason: 'pole', poleId: 'test-pole', amount: 50 });
  assert.deepEqual(f.room.scores(), before);
  f.set('2026-10-27T13:00:00Z');
  assert.deepEqual(f.records[0].players[0], { nick: b.welcome.you.nick, earned: 18, deliveries: 3, rank: 1 });
  assert.ok(f.room.scores().every(s => s.earned === 0 && s.deliveries === 0));
});

test('equal earnings and deliveries prefer the earliest result rather than nickname or join', () => {
  const f = fixture(), a = f.connect(), b = f.connect();
  b.send(delivery);
  f.advance(100);
  a.send(delivery);
  assert.deepEqual(f.room.scores().map(s => s.nick), [b.welcome.you.nick, a.welcome.you.nick]);
});
