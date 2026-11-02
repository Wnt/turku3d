import { expect, test } from 'vitest';
import { FrameClock } from '../../src/core/frameClock';

test('replay time is frame-exact despite slow renders, pauses and readiness frames', () => {
  const a = new FrameClock(123), b = new FrameClock(123);
  a.reset(123); b.reset(123);
  for (let frame = 0; frame < 240; frame++) {
    a.seek(frame / 60); b.seek(frame / 60);
    a.tick(frame * 16.7); b.tick(frame * 2000);
    expect(a.simSeconds).toBe(frame / 60); expect(b.utcMs).toBe(a.utcMs);
    expect(b.tick(frame * 2000 + 1000)).toBe(0);
  }
  expect(() => a.seek(0)).toThrow('Reset');
  a.reset(456); a.seek(1 / 60); a.tick(1e9);
  expect(a.simSeconds).toBe(1 / 60);
});
