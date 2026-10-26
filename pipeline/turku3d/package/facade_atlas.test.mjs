import assert from 'node:assert/strict';
import { test } from 'node:test';
import { atlasUv } from './facade_atlas.mjs';

test('a two-metre door stays at ground level in every atlas row', () => {
  const wallHeight = 10;
  const scale = [1 / 8, -1 / wallHeight];
  const offset = [0, 1];
  const pageHeight = 1024;
  for (const y of [0, 512]) {
    const entry = { x: 128, y, width: 256, height: 400 };
    const base = atlasUv(4, 0, scale, offset, entry, pageHeight);
    const doorTop = atlasUv(4, 2, scale, offset, entry, pageHeight);
    const eaves = atlasUv(4, wallHeight, scale, offset, entry, pageHeight);
    assert.equal(base[1], (y + 4 + 400) / pageHeight);
    assert.equal(doorTop[1], (y + 4 + 320) / pageHeight);
    assert.equal(eaves[1], (y + 4) / pageHeight);
    assert.ok(base[1] > doorTop[1] && doorTop[1] > eaves[1]);
    assert.equal(base[0], doorTop[0]);
  }
});
