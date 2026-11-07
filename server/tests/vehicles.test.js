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

test('reload while parked preserves all personal kinds and renews their retained lifetime', () => {
  const f = fixture(), a = f.connect(); a.send(pose);
  for (const mode of ['car', 'scooter', 'truck']) a.send(car(a, {
    id: `${a.welcome.you.id}:${mode}`, localId: mode,
    pose: { ...pose, mode, vehicle: mode === 'car' ? 'player' : mode, E: 178, vE: 0 },
  }));
  // Just short of expiry, so disconnect grace must also protect empty cars.
  f.advance(VEHICLE_LIMITS.lifetime - 1000);
  f.room.disconnect(a.c);
  f.advance(2000);
  const b = f.connect(a.welcome.session);
  assert.equal(b.welcome.you.id, a.welcome.you.id);
  assert.equal(b.welcome.vehicles.upsert.length, 3);
  assert.ok(b.welcome.vehicles.upsert.every(v => v.owner === b.welcome.you.id && !v.occupant && v.pose.E === 178));
  f.advance(VEHICLE_LIMITS.reconnectMs);
  assert.equal(f.room.vehicles.entries.size, 3);
  const c = f.connect(); c.send(pose); f.room.flushPoses();
  assert.equal(c.welcome.vehicles.upsert.length, 3);
});

test('an expired parked lifetime is retained only until the disconnected owner grace ends', () => {
  const f = fixture(), a = f.connect(); a.send(pose); a.send(car(a));
  f.advance(VEHICLE_LIMITS.lifetime - 1000);
  f.room.disconnect(a.c);
  f.advance(VEHICLE_LIMITS.reconnectMs - 1);
  assert.equal(f.room.vehicles.entries.size, 1);
  f.advance(1);
  assert.equal(f.room.vehicles.entries.size, 0);
  assert.equal(f.connect(a.welcome.session).welcome.vehicles.upsert.length, 0);
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

test('bot cars never pile up: gone when empty for a while or when their bot is offline; player cars stay', () => {
  const f = fixture(), bot = f.connect(undefined, { bot: true }), player = f.connect();
  bot.send(pose); player.send(pose);
  bot.send(car(bot, { action: 'acquire' })); player.send(car(player, { action: 'acquire' }));
  const botCar = result(bot).vehicle.id, playerCar = result(player).vehicle.id;
  // Seated and connected: kept.
  f.advance(VEHICLE_LIMITS.botEmptyMs + 1);
  assert.ok(f.room.vehicles.entries.has(botCar));
  // Released: removed once empty for botEmptyMs; the player's released car stays.
  bot.send({ type: 'vehicle', action: 'release', id: botCar, request: 'leave' });
  player.send({ type: 'vehicle', action: 'release', id: playerCar, request: 'leave' });
  f.advance(VEHICLE_LIMITS.botEmptyMs + 1);
  assert.equal(f.room.vehicles.entries.has(botCar), false);
  assert.ok(f.room.vehicles.entries.has(playerCar));
  // A bot that crashes (disconnects) while seated: its car goes after botOfflineMs, not the 5 min reservation.
  const crashed = f.connect(undefined, { bot: true }); crashed.send(pose);
  crashed.send(car(crashed, { action: 'acquire' }));
  const crashedCar = result(crashed).vehicle.id;
  f.room.disconnect(crashed.c);
  f.advance(VEHICLE_LIMITS.botOfflineMs + 1);
  assert.equal(f.room.vehicles.entries.has(crashedCar), false);
});

test('player-spawned trucks go once their owner has left; a truck that fell out of the world goes at once', () => {
  const f = fixture(), a = f.connect(), b = f.connect(); a.send(pose); b.send(pose);
  a.send(car(a, { id: `${a.welcome.you.id}:truck`, localId: 'truck', pose: { ...pose, mode: 'truck', vehicle: 'truck', vE: 0 } }));
  a.send(car(a, { id: `${a.welcome.you.id}:car`, localId: 'car', pose: { ...pose, mode: 'car', vehicle: 'player', vE: 0 } }));
  b.send(car(b, { id: `${b.welcome.you.id}:truck`, localId: 'truck', pose: { ...pose, mode: 'truck', vehicle: 'truck', vE: 0, H: -2037 } }));
  f.advance(1);
  // Empty and below the world floor: removed; the connected owner's parked truck stays.
  assert.equal(f.room.vehicles.entries.has(`${b.welcome.you.id}:truck`), false);
  f.advance(VEHICLE_LIMITS.abandonedEmptyMs);
  assert.ok(f.room.vehicles.entries.has(`${a.welcome.you.id}:truck`));
  f.room.disconnect(a.c);
  f.advance(VEHICLE_LIMITS.reconnectMs - 1);
  assert.ok(f.room.vehicles.entries.has(`${a.welcome.you.id}:truck`));
  f.advance(2);
  assert.equal(f.room.vehicles.entries.has(`${a.welcome.you.id}:truck`), false);
  // The starter car has its own ten minutes (next test).
  assert.ok(f.room.vehicles.entries.has(`${a.welcome.you.id}:car`));
});

test('cars a departed player left empty go five minutes after both; not while the owner plays or someone sits in one', () => {
  const f = fixture(), a = f.connect(), b = f.connect(); a.send(pose); b.send(pose);
  const kinds = [
    { id: 'park:t0_0_0:GIS:12', source: 't0_0_0:GIS:12', localId: 't0_0_0:GIS:12', pose: { ...pose, mode: 'car', vehicle: 'sedan/red', vE: 0 } },
    { id: 'park:tr:t0_0_0:7:3', source: 'tr:t0_0_0:7:3', localId: 'tr:t0_0_0:7:3', pose: { ...pose, mode: 'car', vehicle: 'prius-2012/white', vE: 0 } },
    { id: `${a.welcome.you.id}:scooter`, source: null, localId: 'scooter', pose: { ...pose, mode: 'scooter', vehicle: 'scooter', vE: 0 } },
  ];
  for (const k of kinds) a.send(car(a, k));
  a.send(car(a, { id: 'park:t0_0_0:GIS:13', source: 't0_0_0:GIS:13', localId: 't0_0_0:GIS:13', pose: { ...pose, mode: 'car', vehicle: 'sedan/blue', vE: 0 } }));
  assert.equal(f.room.vehicles.entries.size, 4);
  // The owner plays on: its empty cars keep the hour lifetime.
  f.advance(VEHICLE_LIMITS.abandonedEmptyMs * 2);
  assert.equal(f.room.vehicles.entries.size, 4);
  // b sits in one of them; a leaves. Within the reconnect grace nothing goes.
  b.send({ type: 'vehicle', action: 'acquire', id: 'park:t0_0_0:GIS:13', request: 'take' });
  assert.equal(result(b).ok, true);
  f.room.disconnect(a.c);
  f.advance(VEHICLE_LIMITS.reconnectMs - 1000);
  assert.equal(f.room.vehicles.entries.size, 4);
  f.advance(2000);
  // Past the grace and empty for five minutes: gone, and their slots are free again; b's ride stays.
  assert.deepEqual([...f.room.vehicles.entries.keys()], ['park:t0_0_0:GIS:13']);
  assert.deepEqual(f.room.vehicles.sources(), ['t0_0_0:GIS:13']);
});

test('a departed player\'s starter car goes ten minutes after it left, unless someone else drives it', () => {
  const f = fixture(), a = f.connect(), b = f.connect(), c = f.connect(); a.send(pose); b.send(pose); c.send(pose);
  const starter = client => `${client.welcome.you.id}:player`;
  for (const client of [a, b, c]) client.send(car(client, { id: starter(client), localId: 'player', pose: { ...pose, mode: 'car', vehicle: 'player', vE: 0 } }));
  // b drives off in its own car and leaves while seated; c takes a's empty car.
  b.send(car(b, { id: starter(b), localId: 'player', action: 'acquire' }));
  f.room.disconnect(a.c); f.room.disconnect(b.c);
  f.advance(VEHICLE_LIMITS.starterOfflineMs - 1000);
  c.send({ type: 'vehicle', action: 'acquire', id: starter(a), request: 'take' });
  assert.equal(result(c).ok, true);
  f.advance(2000);
  // b's seat was freed after the reconnect grace; its car goes ten minutes after b left. a's car has a driver.
  assert.equal(f.room.vehicles.entries.has(starter(b)), false);
  assert.ok(f.room.vehicles.entries.has(starter(a)));
  // The connected player's own empty car stays.
  assert.ok(f.room.vehicles.entries.has(starter(c)));
  // An owner who comes back within the ten minutes keeps theirs.
  const d = f.connect(); d.send(pose);
  d.send(car(d, { id: starter(d), localId: 'player', pose: { ...pose, mode: 'car', vehicle: 'player', vE: 0 } }));
  f.room.disconnect(d.c);
  f.advance(VEHICLE_LIMITS.starterOfflineMs / 2);
  const back = f.connect(d.welcome.session);
  f.advance(VEHICLE_LIMITS.starterOfflineMs);
  assert.equal(back.welcome.you.id, d.welcome.you.id);
  assert.ok(f.room.vehicles.entries.has(starter(d)));
});

test('a trip never leaves a seat behind: moving out frees it at once, an arrival hello frees a reserved one', () => {
  const f = fixture(), a = f.connect(), b = f.connect(); a.send(pose); b.send(pose);
  a.send(car(a, { id: 'park:bay', source: 'bay', action: 'acquire' }));
  assert.equal(result(a).ok, true);
  // Holding a seat, a player cannot take another (the stale seat that blocked every car after a trip).
  a.send(car(a, { id: 'park:other', source: 'other', action: 'acquire' }));
  assert.equal(result(a).ok, false);
  f.room.moveOut(a.c);
  assert.equal(f.room.vehicles.entries.get('park:bay').occupant, null);
  b.send({ type: 'vehicle', action: 'acquire', id: 'park:bay', request: 'next' });
  assert.equal(result(b).ok, true);
  // A socket that closed (no move) keeps the five-minute reservation; a plain reconnect resumes it ...
  b.send({ type: 'vehicle', action: 'release', id: 'park:bay', request: 'leave' });
  const c = f.connect(); c.send(pose);
  c.send(car(c, { id: 'park:bay', source: 'bay', action: 'acquire' }));
  f.room.disconnect(c.c);
  const back = f.connect(c.welcome.session);
  assert.equal(back.welcome.you.id, c.welcome.you.id);
  assert.equal(f.room.vehicles.entries.get('park:bay').occupant, c.welcome.you.id);
  // ... and an arrival from another city frees it before the welcome.
  f.room.disconnect(back.c);
  const arrived = f.connect(c.welcome.session, { arrival: true });
  assert.equal(arrived.welcome.you.id, c.welcome.you.id);
  assert.equal(f.room.vehicles.entries.get('park:bay').occupant, null);
  assert.equal(arrived.welcome.vehicles.upsert.find(v => v.id === 'park:bay').occupant, null);
  arrived.send({ ...pose, E: 180 });
  arrived.send(car(arrived, { id: 'park:other', source: 'other', action: 'acquire', pose: { ...pose, E: 180, mode: 'car', vehicle: 'sedan/red', vE: 0 } }));
  assert.equal(result(arrived).ok, true);
  // A malformed arrival flag is rejected like any malformed hello.
  const rejected = f.room.stats.rejected;
  f.connect(undefined, { arrival: 'yes' });
  assert.equal(f.room.stats.rejected, rejected + 1);
});
