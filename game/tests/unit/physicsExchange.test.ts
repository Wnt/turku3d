import { expect, test } from 'vitest';
import { PhysicsClock, SharedMailbox } from '../../src/physics/exchange';

test('shared snapshots keep UTF-8 IDs, arrays and optional fields; latest write wins', () => {
  const writer = SharedMailbox.create<{ id: string; cars: number[]; optional?: boolean }>(1024);
  const reader = new SharedMailbox(writer.buffer);
  expect(reader.read()).toBeNull();
  writer.write({ id: 'Läntinen', cars: [1, 2], optional: true });
  writer.write({ id: 'Turku', cars: [3] });
  expect(reader.read()).toEqual({ id: 'Turku', cars: [3] });
  expect(reader.read()).toBeNull();
});

test('incomplete writes are never decoded and overflow preserves the last snapshot', () => {
  const writer = SharedMailbox.create<string>(32), reader = new SharedMailbox<string>(writer.buffer);
  writer.write('complete');
  const header = new Int32Array(writer.buffer, 0, 2);
  Atomics.add(header, 0, 1);
  expect(reader.read()).toBeNull();
  Atomics.add(header, 0, 1);
  expect(reader.read()).toBe('complete');
  expect(() => writer.write('x'.repeat(64))).toThrow('overflow');
  expect(reader.read()).toBeNull();
  writer.write('next');
  expect(reader.read()).toBe('next');
});

test('worker clock drops suspended time, caps catch-up and restarts without pause debt', () => {
  const clock = new PhysicsClock();
  expect(clock.advance(100, true)).toBe(0);
  expect(clock.advance(120, true)).toBeCloseTo(0.02);
  expect(clock.advance(4120, true)).toBe(5 / 60);
  expect(clock.advance(4140, false)).toBe(0);
  expect(clock.advance(10000, true)).toBe(0);
  expect(clock.advance(10020, true)).toBeCloseTo(0.02);
});
