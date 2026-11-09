import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { requireNatureNode } from './node-version.mjs';

test('nature builds accept Node 24 and reject other majors with the upgrade command', () => {
  for (const version of ['24.0.0', '24.11.1', '24.18.0']) assert.doesNotThrow(() => requireNatureNode(version));
  for (const version of ['22.18.0', '25.0.0', '26.1.0']) {
    assert.throws(() => requireNatureNode(version), { message: new RegExp(`require Node.js 24.x; found ${version}.*nvm install 24`) });
  }
});

for (const script of ['build-nature-assets.mjs', 'bake-tree-impostors.mjs']) {
  test(`${script} checks the runtime before reading sources or creating output`, () => {
    const scratch = mkdtempSync(path.join(os.tmpdir(), 'nature-node-test-'));
    const out = path.join(scratch, 'output');
    try {
      const url = new URL(`../${script}`, import.meta.url).href;
      const result = spawnSync(process.execPath, ['--input-type=module', '-e',
        `Object.defineProperty(process.versions, 'node', { value: '22.18.0' }); await import(${JSON.stringify(url)});`,
        '--', '--src', path.join(scratch, 'missing-sources'), '--out', out], { encoding: 'utf8' });
      assert.equal(result.status, 1);
      assert.match(result.stderr, /Nature asset builds require Node.js 24.x; found 22.18.0/);
      assert.equal(existsSync(out), false);
    } finally { rmSync(scratch, { recursive: true }); }
  });
}
