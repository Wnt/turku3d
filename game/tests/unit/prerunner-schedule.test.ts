import { expect, test } from 'vitest';
import { CYCLE_EPOCH, DAY_MS } from '../../src/core/dayCycle';
import { prerunnerPresent } from '../../src/world/prerunnerSchedule';

test('rare racer has one full shared-day appearance per four days, identical for every peer', () => {
  for (const id of ['park:t0_0_0:17', 'park:t0_-2_4:127', 'garage:korpi']) {
    for (const start of [-12, 0, 12]) {
      const days = Array.from({ length: 4 }, (_, i) => CYCLE_EPOCH + (start + i) * DAY_MS);
      expect(days.filter(utc => prerunnerPresent(id, utc))).toHaveLength(1);
      for (const utc of days) {
        expect(prerunnerPresent(`${id}`, utc)).toBe(prerunnerPresent(id, utc));
        expect(prerunnerPresent(id, utc + DAY_MS - 1)).toBe(prerunnerPresent(id, utc));
        expect(prerunnerPresent(id, utc + 4 * DAY_MS)).toBe(prerunnerPresent(id, utc));
      }
    }
  }
});

test('stable ids distribute rare racers across all shared-day phases', () => {
  const counts = Array.from({ length: 4 }, (_, day) => Array.from({ length: 1000 }, (_, i) =>
    prerunnerPresent(`park:t0_1_2:${i}`, CYCLE_EPOCH + day * DAY_MS)).filter(Boolean).length);
  expect(counts.reduce((a,b) => a+b, 0)).toBe(1000);
  expect(counts.every(n => n >= 230 && n <= 270)).toBe(true);
});
