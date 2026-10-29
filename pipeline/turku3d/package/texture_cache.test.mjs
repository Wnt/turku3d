import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { useTextureCache, retainTextureCache } from './texture_cache.mjs';

test('geometry edits reuse encoded textures; only two package generations survive', () => {
  const textureCache = fs.mkdtempSync(path.join(os.tmpdir(), 'texture-cache-'));
  try {
    for (const names of [['a', 'shared'], ['b', 'shared'], ['c', 'shared']]) {
      const request = { textureCache };
      for (const name of names) {
        const file = path.join(textureCache, name);
        if (!fs.existsSync(file)) fs.writeFileSync(file, name);
        assert.equal(fs.readFileSync(useTextureCache(request, file), 'utf8'), name);
      }
      retainTextureCache(request);
    }
    assert.deepEqual(fs.readdirSync(textureCache).sort(), ['.retained.json', 'b', 'c', 'shared']);
  } finally { fs.rmSync(textureCache, { recursive:true, force:true }); }
});
