// Entry for the delivery bots (tools/drivetest/bots.sh): BOTS_URL, BOTS_ORIGIN, DRIVE_DIR (tiles, tile-service.json,
// navigation.json), BOTS_N, BOTS_STATUS, BOTS_STOP. Never part of the unit suite.
import { test } from 'vitest';
import { runDeliveries } from './deliveries';

test.runIf(!!process.env.BOTS_URL)('delivery bots', async () => {
  await runDeliveries({ url: process.env.BOTS_URL!, origin: process.env.BOTS_ORIGIN ?? 'https://city.lab.madekivi.fi',
    dir: process.env.DRIVE_DIR!, status: process.env.BOTS_STATUS!, stop: process.env.BOTS_STOP!, bots: Number(process.env.BOTS_N ?? 5) });
}, 7 * 24 * 3600_000);
