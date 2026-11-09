import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { requireCalibrationTools } from './calibration-tools.mjs';

for (const [missing, description] of [['python3', 'Python 3 with numpy and Pillow'], ['ktx', 'KTX-Software ktx'], ['toktx', 'KTX-Software toktx']]) {
  test(`missing ${missing} stops the build with a recovery command`, () => {
    assert.throws(() => requireCalibrationTools(command => {
      if (command === missing) throw new Error('not available');
    }), error => error.message.includes(description) && error.message.includes('pipeline/env/run.sh'));
  });
}

test('the full build rejects missing calibration tools before creating output, previews or source caches', () => {
  const scratch = mkdtempSync(path.join(os.tmpdir(), 'nature-calibration-test-'));
  const out = path.join(scratch, 'output'), src = path.join(scratch, 'sources'), previews = path.join(scratch, 'previews');
  try {
    const result = spawnSync(process.execPath, [fileURLToPath(new URL('../build-nature-assets.mjs', import.meta.url)),
      '--src', src, '--out', out, '--previews', previews], { encoding: 'utf8', timeout: 10000, env: { ...process.env, PATH: scratch } });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Nature asset build requires Python 3 with numpy and Pillow/);
    for (const directory of [src, out, previews]) assert.equal(existsSync(directory), false);
  } finally { rmSync(scratch, { recursive: true }); }
});
