import test from 'node:test';
import assert from 'node:assert/strict';
import { fixture, pose } from './helpers.js';
import { WATCH } from '../watch.js';

const watches = client => client.messages.filter(m => m.type === 'watch');

test('once a second every client hears the same stamped list of online players\' rounded positions', () => {
  const f = fixture(), a = f.connect(), b = f.connect();
  a.send({ ...pose, E: 173.4, N: -252.6 });
  b.send({ ...pose, t: 2, E: -10.2, N: 40.7 });
  f.advance(WATCH.periodMs);
  const wa = watches(a).at(-1), wb = watches(b).at(-1);
  assert.deepEqual(wa, wb);
  assert.equal(wa.at % WATCH.periodMs, 0);
  assert.deepEqual(wa.points, [[-10, 41], [173, -253]]);
  const n = watches(a).length;
  f.advance(10); // same second: nothing new
  assert.equal(watches(a).length, n);
  f.room.disconnect(b.c);
  f.advance(WATCH.periodMs);
  assert.deepEqual(watches(a).at(-1).points, [[173, -253]]);
});
