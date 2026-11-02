#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const result = spawnSync(process.execPath, [
  '--experimental-transform-types', '--import',
  fileURLToPath(new URL('../src/ts-runtime.mjs', import.meta.url)),
  fileURLToPath(new URL('../src/bridge-crossing.ts', import.meta.url)),
  ...process.argv.slice(2),
], { stdio: 'inherit' });
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
