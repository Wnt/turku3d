import { test } from 'node:test';
import assert from 'node:assert/strict';
import { captureIdentity, residentTilesMatch, waitForTileManifest } from './loop-capture-identity.mjs';

test('capture identity invalidates evidence when source, assets, tiles or view changes', () => {
  const original = { build: 'a', nature: 'oak', tiles: { t0_0_0: 'old' }, pose: '1,2,3', time: '13:00', quality: 'high',
    scenery: { sources: ['one'], missing: ['two'] }, viewport: [1920, 1200, 1] };
  const id = captureIdentity(original);
  assert.equal(id, captureIdentity(Object.fromEntries(Object.entries(original).reverse())));
  for (const patch of [{ build: 'b' }, { nature: 'birch' }, { tiles: { t0_0_0: 'new' } }, { pose: '4,5,6' },
    { time: '22:00' }, { quality: 'medium' }, { viewport: [1280, 720, 1] }, { scenery: { sources: ['one', 'two'], missing: [] } }])
    assert.notEqual(id, captureIdentity({ ...original, ...patch }));
});

test('a full but stale resident model cannot validate a new manifest capture', () => {
  const tiles = [{ id: 't0_0_0', full: true, visual_sha256: 'old' }];
  assert.equal(residentTilesMatch(tiles, { t0_0_0: 'new' }), false);
  assert.equal(residentTilesMatch(tiles, { t0_0_0: 'old' }), true);
  assert.equal(residentTilesMatch(tiles, { t0_0_0: 'old', t0_1_0: 'other' }), false);
});

test('a farm build and a first pass must finish before capture inputs are accepted', async () => {
  const full = { pass_kind: 'full', visual: { t0_0_0: { sha256: 'ready' } } };
  const replies = [new Response('{}', { status: 202 }),
    new Response(JSON.stringify({ ...full, pass_kind: 'first' })), new Response(JSON.stringify(full))];
  let waits = 0;
  const result = await waitForTileManifest('http://local/tile', 't0_0_0', 1000,
    async () => replies.shift(), async () => { waits++; });
  assert.deepEqual(result, full);
  assert.equal(waits, 2);
});

test('a failed farm build is surfaced without accepting a stale fallback', async () => {
  await assert.rejects(waitForTileManifest('http://local/tile', 't0_0_0', 1000,
    async () => new Response('{}', { status: 422 })), /HTTP 422/);
});
