import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, delivery, pose } from './helpers.js';
import { COUNTDOWN_MS } from '../rounds.js';
import { ScoreStore } from '../store.js';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

function setup(n = 4, date) {
  const f = fixture(date), players = Array.from({ length: n }, () => f.connect());
  const command = (i, action, minutes) => players[i].send({ type: 'round', action, minutes, proposal: f.room.rounds.proposal?.id });
  return { ...f, players, command };
}

test('half of currently connected players agree; only initiator starts in five-minute steps', () => {
  const f = setup(); const { room, command } = f;
  command(0, 'propose'); command(0, 'start', 5); assert.equal(room.rounds.active, null);
  command(1, 'agree'); command(1, 'agree'); assert.equal(room.rounds.snapshot().proposal.votes.length, 2);
  command(1, 'start', 5); assert.equal(room.rounds.active, null);
  for (const minutes of [0, 1, 6, 5.5, 185, '5']) command(0, 'start', minutes);
  assert.equal(room.rounds.active, null);
  command(0, 'start', 5); assert.equal(room.rounds.active.minutes, 5);
  assert.equal(room.rounds.active.phase, 'countdown');
});

test('odd electorate, disconnect recalculation, initiator departure and late joins', () => {
  const f = setup(5); f.command(0, 'propose'); f.command(1, 'agree');
  assert.equal(f.room.rounds.snapshot().proposal.needed, 3);
  f.room.disconnect(f.players[4].c); assert.equal(f.room.rounds.snapshot().proposal.needed, 2);
  f.command(0, 'start', 5); f.advance(COUNTDOWN_MS);
  const late = f.connect(); assert.equal(late.welcome.rounds.active.phase, 'running');
  assert.equal(late.welcome.rounds.active.end, f.room.rounds.active.end);
  f.command(0, 'propose'); f.room.disconnect(f.players[0].c); assert.equal(f.room.rounds.proposal, null);
  f.command(1, 'propose'); assert.ok(f.room.rounds.proposal);
});

test('start wipes gameplay and stale messages; countdown locks play; restart archives and wipes again', () => {
  const f = setup(2), p = f.players[0].c.player;
  f.players[0].send(delivery); f.players[0].send(pose);
  p.finedPoles.add('pole'); f.room.brokenPoles.set('pole', {});
  f.room.vehicles.entries.set('old', {});
  f.command(0, 'propose'); f.command(0, 'start', 5);
  const epoch = f.room.rounds.epoch;
  assert.equal(p.earned, 0); assert.equal(p.deliveries, 0); assert.equal(p.orders.size, 0);
  assert.equal(p.finedPoles.size, 0); assert.equal(p.pose, null); assert.equal(f.room.vehicles.entries.size, 0);
  assert.equal(f.room.brokenPoles.size, 0);
  f.players[0].send({ ...delivery, epoch }); assert.equal(p.earned, 0);
  f.advance(COUNTDOWN_MS); f.players[0].send(delivery); assert.equal(p.earned, 0);
  f.players[0].send({ ...delivery, epoch }); assert.equal(p.earned, 14);
  f.command(1, 'propose'); f.command(1, 'start', 10);
  assert.notEqual(f.room.rounds.epoch, epoch); assert.equal(p.earned, 0);
  assert.equal(f.room.rounds.history[0].reason, 'restarted'); assert.equal(f.room.rounds.history[0].scores[0].earned, 14);
});

test('round suppresses hourly reset; deadline saves immutable results and normal play resumes', () => {
  const f = setup(2, '2026-10-27T12:59:58Z');
  f.command(0, 'propose'); f.command(0, 'start', 5); f.advance(COUNTDOWN_MS);
  assert.equal(f.records.length, 0); assert.equal(f.players[0].messages.some(m => m.type === 'hour_reset'), false);
  f.players[0].send({ ...delivery, epoch: f.room.rounds.epoch });
  f.advance(300000);
  assert.equal(f.room.rounds.active, null); assert.equal(f.room.rounds.history.length, 1);
  assert.equal(f.room.rounds.history[0].scores[0].earned, 14);
  assert.equal(f.players[1].messages.filter(m => m.type === 'round_result').length, 1);
  f.players[0].send({ ...delivery, orderId: 'next', epoch: f.room.rounds.epoch });
  assert.equal(f.room.rounds.history[0].scores[0].earned, 14);
  f.set('2026-10-27T14:00:00Z'); assert.equal(f.records.length, 1);
});

test('completed rounds survive score-store reload without entering hourly index', () => {
  const dir = mkdtempSync(join(tmpdir(), 'rounds-'));
  try {
    const store = new ScoreStore(dir); store.writeRounds([{ id: 'round', scores: [] }]);
    assert.deepEqual(new ScoreStore(dir).rounds(), [{ id: 'round', scores: [] }]); assert.deepEqual(store.index(), []);
  } finally { rmSync(dir, { recursive: true }); }
});
