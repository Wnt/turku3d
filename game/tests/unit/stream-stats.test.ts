import { expect, test } from 'vitest';
import { StreamStats } from '../../src/world/streamStats';

test('stream stats: request-to-draw p50/p90 over the last 30 s, the backlog and its oldest wait', () => {
  const stats = new StreamStats();
  for (let i = 1; i <= 10; i++) stats.readyAt(1000, 1000 + i * 100);
  stats.fullAt(1000, 5000);
  stats.readyAt(undefined, 5000);
  let s = stats.snapshot(6000, [2000, 4500], 3);
  expect(s.readyMs).toEqual({ n: 10, p50: 500, p90: 900 });
  expect(s.fullMs).toEqual({ n: 1, p50: 4000, p90: 4000 });
  expect(s).toMatchObject({ pending: 2, installing: 3, oldestPendingS: 4, drawn: 10, full: 1 });
  // Samples older than 30 s leave the percentiles; the totals stay.
  s = stats.snapshot(40_000, [], 0);
  expect(s.readyMs).toEqual({ n: 0, p50: null, p90: null });
  expect(s).toMatchObject({ pending: 0, oldestPendingS: 0, drawn: 10 });
});
