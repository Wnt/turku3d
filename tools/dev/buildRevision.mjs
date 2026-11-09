import { execFileSync } from 'node:child_process';

/** Release archives have no Git objects. Deploy supplies their immutable source revision explicitly. */
export function buildRevision(cwd, archiveCommit = process.env.TURKU3D_BUILD_COMMIT) {
  if (archiveCommit !== undefined) {
    if (!/^[0-9a-f]{40}$/.test(archiveCommit)) throw new Error('TURKU3D_BUILD_COMMIT must be a full 40-character Git commit SHA');
    return { commit: archiveCommit, stamp: archiveCommit };
  }
  const git = args => execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  const commit = git(['rev-parse', 'HEAD']);
  const dirty = git(['status', '--porcelain', '--untracked-files=no']) !== '';
  return { commit, stamp: commit + (dirty ? '+dirty' : '') };
}
