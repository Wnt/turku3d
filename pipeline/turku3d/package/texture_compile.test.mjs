import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { compileTexture, pruneCompiledTextures } from './texture_compile.mjs';
import { codecRunner, completed } from './codec.mjs';

test('tiles share one complete texture and changed content compiles independently', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'compile-'));
  try {
    const requests = ['a', 'b'].map(tile => ({ textureCache:path.join(root, tile), sharedTextureCache:path.join(root, 'shared') }));
    let calls = 0;
    const produce = async file => {
      calls++;
      fs.writeFileSync(file, 'partial');
      await delay(20);
      fs.appendFileSync(file, '-complete');
    };
    const files = await Promise.all(requests.map(r => compileTexture(r, 'a'.repeat(64), 'ktx2', produce)));
    assert.equal(calls, 1);
    assert.equal(files[0], files[1]);
    assert.equal(fs.readFileSync(files[0], 'utf8'), 'partial-complete');
    await compileTexture(requests[0], 'b'.repeat(64), 'ktx2', produce);
    assert.equal(calls, 2);
    assert.equal(fs.readdirSync(requests[0].sharedTextureCache).length, 2);
  } finally { fs.rmSync(root, { recursive:true, force:true }); }
});

test('failed and terminated compilation never poisons a later request', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'compile-'));
  const request = { textureCache:root };
  const key = 'a'.repeat(64);
  try {
    await assert.rejects(compileTexture(request, key, 'ktx2', async file => {
      fs.writeFileSync(file, 'broken'); throw new Error('codec failure');
    }), /codec failure/);
    assert.deepEqual(fs.readdirSync(root), []);
    fs.writeFileSync(path.join(root, `${key}.ktx2.lock`), JSON.stringify({ pid:2147483647 }));
    const file = await compileTexture(request, key, 'ktx2', async target => fs.writeFileSync(target, 'valid'));
    assert.equal(fs.readFileSync(file, 'utf8'), 'valid');
    assert.deepEqual(fs.readdirSync(root), [`${key}.ktx2`]);
  } finally { fs.rmSync(root, { recursive:true, force:true }); }
});

test('shared cache evicts old unused encodes while preserving recently used assets', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'compile-'));
  try {
    const old = path.join(root, `${'a'.repeat(64)}.ktx2`), recent = path.join(root, `${'b'.repeat(64)}.ktx2`);
    for (const file of [old, recent]) fs.writeFileSync(file, '1234');
    fs.utimesSync(old, 1, 1);
    pruneCompiledTextures(root, Date.now(), 4);
    assert.deepEqual(fs.readdirSync(root), [path.basename(recent)]);
  } finally { fs.rmSync(root, { recursive:true, force:true }); }
});

test('codec errors wait for sibling jobs and release queue capacity', async () => {
  const run = codecRunner(1);
  let siblingFinished = false;
  await assert.rejects(completed([
    run(process.execPath, ['-e', 'process.exit(7)']),
    run(process.execPath, ['-e', 'setTimeout(() => {}, 30)']).then(() => { siblingFinished = true; }),
  ]), /7/);
  assert.equal(siblingFinished, true);
  await run(process.execPath, ['-e', 'process.exit(0)']);
});
