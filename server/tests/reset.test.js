import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, pose, delivery } from './helpers.js';

const result = c => c.messages.filter(m => m.type === 'vehicle_result').at(-1);
const last = (c, type) => c.messages.filter(m => m.type === type).at(-1);
function vehicle(client, localId, change = {}) {
  return { type: 'vehicle', action: 'acquire', request: `r-${localId}`, id: `${client.welcome.you.id}:${localId}`, localId,
    source: null, pose: { ...pose, mode: 'car', vehicle: 'player', vE: 0 }, ...change };
}

test('reset zeroes the score, frees the seat and removes own vehicles; identity, world cars and order IDs stay', () => {
  const f = fixture(), a = f.connect(), b = f.connect();
  a.send(pose); b.send(pose);
  a.send(delivery); a.send({ ...delivery, orderId: 'order-2:stop-0' }); b.send({ ...delivery, orderId: 'b-1:stop-0' });
  const you = a.welcome.you;
  assert.deepEqual(last(b, 'scores').scores.map(s => [s.nick, s.earned]), [[you.nick, 28], [b.welcome.you.nick, 14]]);
  // The start car (driven), a bought scooter, a borrowed world car, and a scooter another player now drives.
  a.send(vehicle(a, 'scooter', { action: 'register', pose: { ...pose, mode: 'scooter', vehicle: 'scooter' } }));
  a.send(vehicle(a, 'truck', { action: 'register', pose: { ...pose, mode: 'truck', vehicle: 'truck' } }));
  b.send({ type: 'vehicle', action: 'acquire', request: 'take', id: `${you.id}:truck` });
  assert.equal(result(b).ok, true);
  a.send(vehicle(a, 'park', { id: 'park:tile:bay', source: 'tile:bay', action: 'register', pose: { ...pose, mode: 'car', vehicle: 'sedan/red' } }));
  a.send(vehicle(a, 'player'));
  assert.equal(result(a).vehicle.occupant, you.id);
  const before = b.messages.length;

  a.send({ type: 'reset' });
  const p = f.room.players.get(you.id);
  assert.deepEqual([p.earned, p.deliveries, p.pose], [0, 0, null]);
  assert.deepEqual([...f.room.vehicles.entries.keys()].sort(), [`${you.id}:truck`, 'park:tile:bay']);
  assert.equal(f.room.vehicles.entries.get('park:tile:bay').occupant, null);
  assert.equal(f.room.vehicles.entries.get(`${you.id}:truck`).occupant, b.welcome.you.id);
  // The sender hears the removals before the answer; everyone gets the zeroed score and the roster.
  const types = a.messages.slice(-4).map(m => m.type);
  assert.deepEqual(types, ['vehicles', 'progress_reset', 'players', 'scores']);
  assert.deepEqual(a.messages.at(-4).remove.sort(), [`${you.id}:player`, `${you.id}:scooter`]);
  const seen = b.messages.slice(before);
  assert.deepEqual(seen.find(m => m.type === 'vehicles').remove.sort(), [`${you.id}:player`, `${you.id}:scooter`]);
  assert.deepEqual(last(b, 'scores').scores.find(s => s.nick === you.nick), { nick: you.nick, earned: 0, deliveries: 0, rank: 2, online: true });
  assert.equal(last(b, 'players').players.find(q => q.id === you.id).pose, null);
  // Same identity; an already paid order stays paid.
  assert.deepEqual([p.id, p.nick, p.session], [you.id, you.nick, a.welcome.session]);
  a.send(delivery);
  assert.equal(p.earned, 0);
  a.send({ ...delivery, orderId: 'order-3:stop-0' });
  assert.equal(p.earned, 14);
});

test('a reset is accepted during a timed round countdown and while the round runs', () => {
  const f = fixture(), a = f.connect(), b = f.connect();
  a.send({ type: 'round', action: 'propose' });
  b.send({ type: 'round', action: 'agree', proposal: f.room.rounds.proposal.id });
  a.send({ type: 'round', action: 'start', proposal: f.room.rounds.proposal.id, minutes: 5 });
  assert.equal(f.room.rounds.active.phase, 'countdown');
  a.send({ type: 'reset' });
  assert.equal(last(a, 'progress_reset').type, 'progress_reset');
});

test('hello with reset:true resets a known session before its welcome; a new session just joins', () => {
  const f = fixture(), a = f.connect();
  a.send(pose); a.send(delivery); a.send(vehicle(a, 'scooter', { pose: { ...pose, mode: 'scooter', vehicle: 'scooter' } }));
  const { session, you } = a.welcome;
  f.room.disconnect(a.c);
  const again = f.connect(session, { reset: true });
  assert.equal(again.welcome.you.id, you.id);
  assert.equal(again.welcome.you.nick, you.nick);
  assert.deepEqual(again.welcome.vehicles.upsert, []);
  assert.equal(again.welcome.players.find(p => p.id === you.id).pose, null);
  assert.equal(again.welcome.scores.find(s => s.nick === you.nick).earned, 0);
  const fresh = f.connect('b'.repeat(64), { reset: true });
  assert.notEqual(fresh.welcome.you.id, you.id);
  assert.equal(f.room.stats.rejected, 0);
});
