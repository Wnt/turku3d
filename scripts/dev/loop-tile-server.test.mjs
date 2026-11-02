import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { tileFiles, createTileServer } from './loop-tile-server.mjs';

test('an unbuilt speculative tile is absent without marking the server unhealthy', async t => {
  const server = createTileServer([]);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const base = `http://127.0.0.1:${server.address().port}/coarse/`;
  const missing = await fetch(`${base}t0_99_99/manifest.json`);
  assert.equal(missing.status, 404);
  assert.equal((await missing.json()).error, 'not built locally');
  const health = await fetch(`${base}health`).then(r => r.json());
  assert.equal(health.state, 'ready');
  assert.equal(health.served.missing, 1);
});

test('a new manifest preserves old immutable downloads without mixing mutable tile files', t => {
  assert.ok(process.env.TURKU3D_WORK, 'source sandbox.env before running');
  const scratch = fs.mkdtempSync(path.join(process.env.TURKU3D_WORK, 'tile-server-test-'));
  t.after(() => fs.rmSync(scratch, { recursive: true, force: true }));
  const roots = ['new', 'old'].map(name => path.join(scratch, name));
  const tile = 't0_0_0';
  for (const root of roots) fs.mkdirSync(path.join(root, tile), { recursive: true });
  fs.writeFileSync(path.join(roots[1], tile, 'manifest.json'), 'old');
  fs.writeFileSync(path.join(roots[1], tile, 'mutable.json'), 'old');
  const asset = `vis.${createHash('sha256').update('old model').digest('hex')}.glb`;
  const previousAsset = path.join(roots[1], tile, asset);
  fs.writeFileSync(previousAsset, 'old model');
  const files = tileFiles(roots);
  assert.equal(files.tileRoot(tile), roots[1]);
  fs.writeFileSync(path.join(roots[0], tile, 'manifest.json'), 'new');
  assert.equal(files.find(`${tile}/manifest.json`), path.join(roots[0], tile, 'manifest.json'));
  assert.equal(files.find(`${tile}/${asset}`), previousAsset);
  assert.equal(files.find(`${tile}/mutable.json`), null);
  assert.equal(files.find(`../old/${tile}/${asset}`), null);
});
