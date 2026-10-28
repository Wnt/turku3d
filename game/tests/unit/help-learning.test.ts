import { expect, test } from 'vitest';
import { helpHidden, recordHelp, type HelpProgress } from '../../src/core/helpLearning';
const keys = ['move', 'look', 'sprint', 'jump', 'interact', 'camera', 'map', 'pause'];
const empty = (): HelpProgress => ({ counts: {}, elapsedMs: 0 });

test('minute boundary hides help, before the boundary keeps it visible', () => {
  expect(helpHidden(recordHelp(empty(), [], 59_999), keys)).toBe(false);
  expect(helpHidden(recordHelp(empty(), [], 60_000), keys)).toBe(true);
  expect(helpHidden(empty(), [])).toBe(false);
});

test('75 percent must be used twice; duplicates in one sample count only once', () => {
  let row = recordHelp(empty(), [...keys.slice(0, 6), 'move'], 0);
  expect(row.counts.move).toBe(1);
  expect(helpHidden(row, keys)).toBe(false);
  row = recordHelp(row, keys.slice(0, 5), 0);
  expect(helpHidden(row, keys)).toBe(false);
  row = recordHelp(row, ['camera'], 0);
  expect(helpHidden(row, keys)).toBe(true);
  expect(helpHidden(row, [...keys, 'extra'])).toBe(false); // ceil(9 * .75) = 7
});

test('mode progress is independent and held-input samples never add uses', () => {
  const foot = recordHelp(recordHelp(empty(), keys, 0), keys, 0);
  const car = recordHelp(empty(), [], 10_000);
  expect(helpHidden(foot, keys)).toBe(true);
  expect(helpHidden(car, keys)).toBe(false);
  expect(recordHelp(car, [], -100)).toEqual(car);
  expect(recordHelp(foot, keys, 120_000).counts.move).toBe(2);
});
