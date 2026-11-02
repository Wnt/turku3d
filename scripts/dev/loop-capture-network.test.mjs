import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AssetTransfers } from './loop-capture-network.mjs';

test('a completed retry recovers a transient distant-asset 502', () => {
  const network = new AssetTransfers();
  network.start('one', 'far.glb'); network.response('one', 502); network.finish('one');
  network.start('two', 'far.glb'); network.response('two', 200);
  assert.equal(network.failures.size, 1, 'headers alone do not prove a completed download');
  network.finish('two');
  assert.equal(network.failures.size, 0);
  assert.equal(network.pending.size, 0);
});

test('failed bodies and unrelated successful assets cannot clear a failure', () => {
  const network = new AssetTransfers();
  network.start('one', 'far.glb'); network.response('one', 200); network.finish('one', 'connection reset');
  network.start('two', 'near.glb'); network.response('two', 200); network.finish('two');
  assert.deepEqual([...network.failures.values()], ['connection reset far.glb']);
});
