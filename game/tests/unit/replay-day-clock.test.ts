import { test, expect, vi } from 'vitest';
import { DayClock } from '../../src/render/dayClock';
import { cycleAt } from '../../src/core/dayCycle';
import type { Environment } from '../../src/render/environment';

test('rewinding replay restores the recorded clock and refreshes future IBL immediately', () => {
  let now = 1000;
  const env = { setSun: vi.fn(), setTime: vi.fn(), updateIBL: vi.fn(), sunElevation: 25, skyDrift: () => 0 };
  const clock = new DayClock(env as unknown as Environment, { kind: 'hour', hour: 14 }, false, () => now);
  clock.resume(480); now = 31000; clock.update();
  expect(clock.hour).toBeCloseTo(18);
  env.updateIBL.mockClear();
  now = 1000; clock.resetTimeline({ kind: 'hour', hour: 14 });
  expect(clock.hour).toBe(14);
  expect(clock.time.toISOString()).toBe(cycleAt(14).time.toISOString());
  expect(env.updateIBL).toHaveBeenCalledOnce();
  expect(clock.hourAt(31000)).toBe(14);
  env.updateIBL.mockClear(); clock.update();
  expect(env.updateIBL).not.toHaveBeenCalled();
});
