import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture } from './helpers.js';

test('broken poles are shared, fined once, joined late and reset at the hour', () => {
  const f = fixture('2026-10-27T12:59:50Z');
  const a = f.connect(), b = f.connect();
  const poleId = 't0_0_0:GIS_Liikennemerkit:123';
  a.send({ type: 'world', action: 'break', poleId, by: a.welcome.you.id, t: 123 });
  assert.deepEqual(b.messages.find(m => m.type === 'world').poleId, poleId);
  const c = f.connect();
  assert.deepEqual(c.welcome.brokenPoles.map(p => p.poleId), [poleId]);
  a.send({ type: 'fine', reason: 'pole', poleId, amount: 50 });
  assert.equal(f.room.scores().find(s => s.nick === a.welcome.you.nick).money, -50);
  a.send({ type: 'fine', reason: 'pole', poleId, amount: 50 });
  assert.equal(f.room.stats.rejected, 1);
  f.advance(10_000);
  assert.deepEqual(f.room.brokenPoles.size, 0);
  assert.deepEqual(a.messages.filter(m => m.type === 'welcome').at(-1).brokenPoles, []);
});
