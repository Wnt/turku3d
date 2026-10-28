import { expect, test } from 'vitest';
import { CYCLE_EPOCH, DAY_MS, TRANSITION_MS, dayCycle } from '../../src/core/dayCycle';
import { sunPosition } from '../../src/core/sun';

const local = (date: Date) => new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Helsinki',
  day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date);

test('UTC phase agrees across cycles, including negative epochs; local date is fixed', () => {
  expect(local(dayCycle(CYCLE_EPOCH).time)).toBe('15/10/2027, 08:45');
  expect(local(dayCycle(CYCLE_EPOCH + DAY_MS / 2).time)).toBe('15/10/2027, 13:07');
  for (const period of [-123, -1, 1, 500]) {
    expect(dayCycle(CYCLE_EPOCH + period * (DAY_MS + TRANSITION_MS)).time.getTime()).toBe(CYCLE_EPOCH);
  }
});

test('ten-second forward skip wraps the clock on the same date but never renders night', () => {
  const evening = dayCycle(CYCLE_EPOCH + DAY_MS);
  expect(evening.transition).toBe(true);
  expect(local(evening.time)).toBe('15/10/2027, 17:30');
  const middle = dayCycle(CYCLE_EPOCH + DAY_MS + TRANSITION_MS / 2);
  expect(local(middle.time)).toBe('15/10/2027, 01:07');
  expect(middle.sunDisc).toBe(0);
  expect(evening.sunDisc).toBe(1);
  const floor = Math.min(sunPosition(evening.lightTime).elevation, sunPosition(new Date(CYCLE_EPOCH)).elevation);
  for (let t = 0; t < TRANSITION_MS; t += 100) {
    const sample = dayCycle(CYCLE_EPOCH + DAY_MS + t);
    expect(local(sample.time).startsWith('15/10/2027')).toBe(true);
    expect(sample.sunDisc).toBeGreaterThanOrEqual(0);
    expect(sample.sunDisc).toBeLessThanOrEqual(1);
    expect(sunPosition(sample.lightTime).elevation).toBeGreaterThanOrEqual(floor - 0.001);
  }
  const morning = dayCycle(CYCLE_EPOCH + DAY_MS + TRANSITION_MS);
  expect(morning.transition).toBe(false);
  expect(morning.sunDisc).toBe(1);
  expect(local(morning.time)).toBe('15/10/2027, 08:45');
  expect(Math.abs(dayCycle(CYCLE_EPOCH + DAY_MS + TRANSITION_MS - 1).lightTime.getTime() - morning.lightTime.getTime())).toBeLessThan(1);
});
