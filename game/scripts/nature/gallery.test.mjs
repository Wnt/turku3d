import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

for (const [name, browserEnv] of [
  ['no leased CDP endpoint', { BROWSER_CDP: '', LEASE_HOST: 'air' }],
  ['a Mac session', { BROWSER_CDP: 'http://127.0.0.1:1', LEASE_HOST: 'mac' }],
]) {
  test(`nature gallery rejects ${name} before creating output or launching Chrome`, () => {
    const scratch = mkdtempSync(path.join(os.tmpdir(), 'nature-gallery-test-'));
    const out = path.join(scratch, 'gallery');
    try {
      const result = spawnSync(process.execPath, [fileURLToPath(new URL('./gallery.mjs', import.meta.url)), '--out', out],
        { encoding: 'utf8', timeout: 10000, env: { ...process.env, ...browserEnv } });
      assert.equal(result.status, 1);
      assert.match(result.stderr, /Nature gallery requires BROWSER_CDP from host-lease\/session.py on Air or Windows/);
      assert.equal(existsSync(out), false);
    } finally { rmSync(scratch, { recursive: true }); }
  });
}
