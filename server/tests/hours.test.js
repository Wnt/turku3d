import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { hourAt, HOUR_MS } from '../hours.js';
import { ScoreStore, RETENTION_MS } from '../store.js';
import { fixture, delivery } from './helpers.js';

for (const [label, before, first, second] of [
  ['spring', '2026-03-29T00:59:49Z', '2026-03-29T02+02', '2026-03-29T04+03'],
  ['autumn', '2026-10-25T00:59:49Z', '2026-10-25T03+03', '2026-10-25T03+02'],
]) test(`${label} DST rollover warns, archives and rerolls; arrival hour receives in-flight pay`, () => {
  const f = fixture(before), a = f.connect(), b = f.connect();
  const oldNick = a.welcome.you.nick, expiredSession = b.welcome.session;
  f.room.disconnect(b.c);
  a.send(delivery);
  assert.equal(f.room.hour.id, first);
  f.advance(1000);
  assert.equal(a.messages.filter(m => m.type === 'hour_end_in').length, 1);
  assert.equal(a.messages.find(m => m.type === 'hour_end_in').seconds, 10);
  f.advance(10_000);
  assert.equal(f.room.hour.id, second);
  assert.equal(f.records[0].hour.id, first);
  assert.equal(f.records[0].players.find(p => p.nick === oldNick).money, 14);
  assert.notEqual(f.room.scores()[0].nick, oldNick);
  assert.equal(f.room.scores()[0].money, 0);
  a.send({ ...delivery, orderId: 'in-flight' });
  a.send(delivery); // Never pay the same already-completed stop across rollover.
  assert.equal(f.room.scores()[0].money, 14);
  assert.equal(f.room.stats.rejected, 1);
  const c = f.connect(expiredSession);
  assert.notEqual(c.welcome.session, expiredSession);
  assert.ok(a.messages.some(m => m.type === 'hour_reset'));
});

test('Helsinki DST days have 23 and 25 distinct real hour IDs', () => {
  for (const [start, hours] of [['2026-03-28T22:00:00Z', 23], ['2026-10-24T21:00:00Z', 25]]) {
    const ids = Array.from({ length: hours }, (_, i) => hourAt(Date.parse(start) + i * HOUR_MS).id);
    assert.equal(new Set(ids).size, hours);
    assert.equal(new Set(ids.map(id => id.slice(0, 10))).size, 1);
    assert.notEqual(hourAt(Date.parse(start) + hours * HOUR_MS).id.slice(0, 10), ids[0].slice(0, 10));
  }
});

test('score files are atomic, chronologically indexed across DST, and pruned after seven days', t => {
  const directory = mkdtempSync(join(tmpdir(), 'turku-mp-store-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const store = new ScoreStore(directory);
  const first = hourAt(Date.parse('2026-10-25T00:00:00Z'));
  const second = hourAt(Date.parse('2026-10-25T01:00:00Z'));
  const players = [{ nick: 'AB', money: 14, deliveries: 1, rank: 1 }];
  store.write(first, players); store.write(second, []);
  assert.deepEqual(store.index(), [second, first]);
  assert.deepEqual(store.read(first.id), { hour: first, players });
  assert.equal(store.read('../anything'), null);
  assert.ok(readdirSync(directory).every(name => name.endsWith('.json')));
  store.prune(Date.parse(first.end) + RETENTION_MS - 1);
  assert.equal(store.index().length, 2);
  store.prune(Date.parse(first.end) + RETENTION_MS);
  assert.deepEqual(store.index(), [second]);
});
