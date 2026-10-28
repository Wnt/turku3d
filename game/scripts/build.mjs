// A public build carries a local production companion for the existing single-player gameplay checks.
// Only build/game is published; build/check-game stays in the frozen internal release/cache.
import { spawnSync } from 'node:child_process';
import { checkGameDist } from '../../tools/dev/environment.mjs';

function run(command, args, env = process.env) {
  const result = spawnSync(command, args, { stdio: 'inherit', env });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
run('npm', ['run', 'build']);
if (process.env.VITE_PUBLIC_RELEASE === '1') {
  run(process.execPath, ['node_modules/vite/bin/vite.js', 'build', '--outDir', checkGameDist],
    { ...process.env, VITE_PUBLIC_RELEASE: '' });
}
