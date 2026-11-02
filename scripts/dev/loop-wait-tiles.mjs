#!/usr/bin/env node
// Wait for fresh farm products before reserving a browser. Capture still verifies resident hashes.
import fs from 'node:fs';
import path from 'node:path';
import { waitForTileManifest } from './loop-capture-identity.mjs';

const args = process.argv.slice(2), options = {}, tiles = [];
for (let i = 0; i < args.length; i++) {
  if (args[i].startsWith('--')) {
    const key = args[i].slice(2);
    if (!['service', 'version', 'timeout', 'out'].includes(key) || !args[i + 1])
      throw new Error(`Unknown or incomplete option: ${args[i]}`);
    options[key] = args[++i];
  } else tiles.push(args[i]);
}
if (!options.service || !options.version || !tiles.length || tiles.some(t => !/^t0_-?\d+_-?\d+$/.test(t)))
  throw new Error('Usage: loop-wait-tiles.mjs --service URL --version REVISION [--timeout SECONDS] [--out FILE] t0_I_J ...');
const timeout = Number(options.timeout ?? 600) * 1000;
if (!Number.isFinite(timeout) || timeout <= 0) throw new Error('Timeout must be positive');
const started = Date.now(), ready = {};
const result = () => ({ requestedVersion: options.version, service: options.service,
  elapsedSeconds: (Date.now() - started) / 1000, ready });
try {
  const outcomes = await Promise.allSettled([...new Set(tiles)].map(async tile => {
    const url = new URL(`/v/${encodeURIComponent(options.version)}/coarse/${tile}/manifest.json`, options.service);
    const manifest = await waitForTileManifest(url, tile, timeout);
    ready[tile] = { revision: manifest.revision ?? null, generation: manifest.generation ?? null,
      sourceVersion: manifest.source_version ?? null, visualHash: manifest.visual[tile].sha256 };
    console.log(JSON.stringify({ tile, ...ready[tile] }));
  }));
  const failed = outcomes.filter(item => item.status === 'rejected');
  if (failed.length) throw new Error(failed.map(item => item.reason.message).join('; '));
  console.log(JSON.stringify({ complete: true, ...result() }));
} catch (error) {
  console.error(JSON.stringify({ complete: false, error: error.message, ...result() }));
  process.exitCode = 1;
} finally {
  if (options.out) {
    fs.mkdirSync(path.dirname(options.out), { recursive: true });
    fs.writeFileSync(options.out, JSON.stringify({ complete: Object.keys(ready).length === new Set(tiles).size,
      ...result() }, null, 2) + '\n');
  }
}
