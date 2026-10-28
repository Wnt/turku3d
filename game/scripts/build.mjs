// Package only. Agents select type checks separately before merging into main.
import { spawnSync } from 'node:child_process';

const result = spawnSync(process.execPath,
  ['node_modules/vite/bin/vite.js', 'build'], { stdio: 'inherit', env: process.env });
if (result.error) throw result.error;
process.exit(result.status ?? 1);
