// Entry for the drive sweep (tools/drivetest/README.md): `npx vitest run -c vitest.drive.config.ts` with
// DRIVE_DIR (sweep folder: tile-service.json, osm-structures.json, cache), DRIVE_SHARD=k/n, DRIVE_LIMIT=lanes,
// DRIVE_ONLY=id|name,... Never part of the unit suite.
import { test } from 'vitest';
import { sweep } from './sweep';

test('drive sweep', async () => {
  const [k, n] = (process.env.DRIVE_SHARD ?? '0/1').split('/').map(Number);
  const result = await sweep({ dir: process.env.DRIVE_DIR!, shard: [k, n],
    limit: process.env.DRIVE_LIMIT ? Number(process.env.DRIVE_LIMIT) : undefined,
    only: process.env.DRIVE_ONLY ? process.env.DRIVE_ONLY.split(',') : undefined });
  console.log(JSON.stringify(result));
}, 24 * 3600_000);
