import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { buildRevision } from './buildRevision.mjs';

test('an archive stamps its supplied revision without a Git repository', () => {
  const commit = '1234567890abcdef1234567890abcdef12345678';
  assert.deepEqual(buildRevision('/no-git-repository-here', commit), { commit, stamp: commit });
});

test('an invalid archive revision fails instead of publishing misleading provenance', () => {
  for (const commit of ['', 'HEAD', '1234567', 'a'.repeat(40) + '+dirty']) {
    assert.throws(() => buildRevision('/no-git-repository-here', commit), /full 40-character Git commit SHA/);
  }
});

test('a worktree refreshes HEAD and tracked dirty state without treating local products as changes', () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'build-revision-'));
  const git = (...args) => execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  try {
    git('init', '-q');
    fs.writeFileSync(path.join(cwd, 'source.txt'), 'first\n');
    const commit = () => {
      git('add', 'source.txt');
      git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'commit.gpgsign=false', 'commit', '-qm', 'source');
      return git('rev-parse', 'HEAD');
    };
    const first = commit();
    assert.deepEqual(buildRevision(cwd, undefined), { commit: first, stamp: first });
    fs.writeFileSync(path.join(cwd, 'untracked-product.txt'), 'product\n');
    assert.equal(buildRevision(cwd, undefined).stamp, first);
    fs.writeFileSync(path.join(cwd, 'source.txt'), 'second\n');
    assert.equal(buildRevision(cwd, undefined).stamp, first + '+dirty');
    const second = commit();
    assert.notEqual(first, second);
    assert.deepEqual(buildRevision(cwd, undefined), { commit: second, stamp: second });
  } finally { fs.rmSync(cwd, { recursive: true }); }
});
