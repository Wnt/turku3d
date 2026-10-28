import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { cachedEncoder } from './encode_cache.mjs';

test('lossless buffer cache keys source, layout, codec and options; rejects corruption', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mesh-cache-'));
  let calls = 0;
  const encoder = { encodeGltfBuffer(source) { calls++; return Buffer.from(source); } };
  const a = cachedEncoder(encoder, 'codec1', () => root);
  const input = Buffer.from([1, 2, 3, 4]);
  try {
    assert.deepEqual(a.encoder.encodeGltfBuffer(input, 1, 4, 'ATTRIBUTES'), input);
    assert.deepEqual(a.encoder.encodeGltfBuffer(input, 1, 4, 'ATTRIBUTES'), input);
    assert.equal(calls, 1);
    a.encoder.encodeGltfBuffer(input, 2, 2, 'ATTRIBUTES');
    a.encoder.encodeGltfBuffer(input, 1, 4, 'TRIANGLES');
    a.encoder.encodeGltfBuffer(input, 1, 4, 'ATTRIBUTES', 1);
    a.encoder.encodeGltfBuffer(Buffer.from([4, 3, 2, 1]), 1, 4, 'ATTRIBUTES');
    cachedEncoder(encoder, 'codec2', () => root).encoder.encodeGltfBuffer(input, 1, 4, 'ATTRIBUTES');
    assert.equal(calls, 6);
    for (const file of fs.readdirSync(root)) fs.writeFileSync(path.join(root, file), 'broken');
    assert.deepEqual(a.encoder.encodeGltfBuffer(input, 1, 4, 'ATTRIBUTES'), input);
    assert.equal(calls, 7);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
