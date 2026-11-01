import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture } from './helpers.js';
import { agree, ROAD_LIMITS } from '../roads.js';

const last = (client, type) => client.messages.filter(m => m.type === type).at(-1);

test('each client hears the hashes every reporting player shares, for its own tiles only', () => {
  const f = fixture(), a = f.connect(), b = f.connect();
  a.send({ type: 'roads', tiles: { t0_0_0: '1.219.255.aa', t0_1_0: '1.10.12.bb' } });
  assert.deepEqual(last(a, 'roads').agreed, { t0_0_0: '1.219.255.aa', t0_1_0: '1.10.12.bb' });
  // B shares t0_0_0 but holds another version of t0_1_0, and one tile A never reported.
  b.send({ type: 'roads', tiles: { t0_0_0: '1.219.255.aa', t0_1_0: '1.10.12.cc', t0_2_0: '1.5.5.dd' } });
  assert.deepEqual(last(a, 'roads').agreed, { t0_0_0: '1.219.255.aa' });
  assert.deepEqual(last(b, 'roads').agreed, { t0_0_0: '1.219.255.aa', t0_2_0: '1.5.5.dd' });
  // When B leaves, A's tile is agreed again.
  f.room.disconnect(b.c);
  assert.deepEqual(last(a, 'roads').agreed, { t0_0_0: '1.219.255.aa', t0_1_0: '1.10.12.bb' });
  assert.equal(f.room.stats.rejected, 0);
});

test('unchanged agreement is not resent; malformed reports are rejected', () => {
  const f = fixture(), a = f.connect();
  a.send({ type: 'roads', tiles: { t0_0_0: 'x' } });
  a.send({ type: 'roads', tiles: { t0_0_0: 'x' } });
  assert.equal(a.messages.filter(m => m.type === 'roads').length, 1);
  for (const tiles of [null, [], { 'bad id': 'x' }, { t0_0_0: 'has space' },
    Object.fromEntries(Array.from({ length: ROAD_LIMITS.tiles + 1 }, (_, i) => [`t0_${i}_0`, 'x']))]) {
    a.send({ type: 'roads', tiles });
  }
  assert.equal(f.room.stats.rejected, 5);
});

test('agreement rule: a conflict removes the tile for everyone', () => {
  const reports = [new Map([['a', '1'], ['b', '2']]), new Map([['a', '1'], ['b', '3']]), new Map([['b', '2']])];
  assert.deepEqual([...agree(reports)], [['a', '1']]);
});
