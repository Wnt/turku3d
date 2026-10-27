// StreetRouter time budget on an unloaded host (make streaming-perf); the route itself is checked in
// tests/unit/router.test.ts, which stays in the fast gate.
import { expect, test } from 'vitest';
import { streetGrid } from '../fixtures/streetGrid';

test('A* stays fast on a 60 x 60 street grid', () => {
  const grid = streetGrid(60);
  const t0 = performance.now();
  grid.route({ x: 3, z: 1 }, { x: 1170, z: 1177 });
  expect(performance.now() - t0).toBeLessThan(50);
});
