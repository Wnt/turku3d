// Entry for the multiplayer bots (tools/drivetest/mp.mjs starts it): MP_URL, MP_ORIGIN, DRIVE_DIR, MP_OUT, MP_BOTS,
// MP_SECONDS, MP_GO. Never part of the unit suite.
import { test } from 'vitest';
import { runBots } from './mpbot';

test.runIf(!!process.env.MP_URL)('multiplayer bots', async () => {
  await runBots({ url: process.env.MP_URL!, origin: process.env.MP_ORIGIN ?? 'http://localhost:5480', dir: process.env.DRIVE_DIR!,
    out: process.env.MP_OUT!, bots: Number(process.env.MP_BOTS ?? 6), seconds: Number(process.env.MP_SECONDS ?? 150), go: process.env.MP_GO });
}, 3600_000);
