import { expect, test } from 'vitest';
import { riverWaveOffsets, riverWavePixels } from '../../src/render/riverWaves';

test('river flow retains sub-second motion at epoch timestamps and wraps without a phase jump', () => {
  const seconds = 1793577600;
  const a = riverWaveOffsets(seconds), b = riverWaveOffsets(seconds + 1 / 60);
  for (let i = 0; i < a.length; i++) {
    expect(a[i]).toBeGreaterThanOrEqual(0); expect(a[i]).toBeLessThan(1);
    const delta = b[i] - a[i];
    const wrapped = delta - Math.round(delta);
    expect(Math.abs(wrapped)).toBeGreaterThan(0.00001);
    expect(Math.abs(wrapped)).toBeLessThan(0.001);
  }
  expect(riverWaveOffsets(seconds)).toEqual(a);
});

test('river wave slopes have no wrap seam or encoded clipping', () => {
  const size = 128, pixels = riverWavePixels(size);
  for (const channel of [0, 1]) {
    let edge = 0, interior = 0;
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
      const v = pixels[(y * size + x) * 4 + channel];
      expect(v).toBeGreaterThan(0); expect(v).toBeLessThan(255);
      if (x === 0) edge += Math.abs(v - pixels[(y * size + size - 1) * 4 + channel]);
      else interior += Math.abs(v - pixels[(y * size + x - 1) * 4 + channel]);
    }
    expect(edge / size).toBeLessThan(interior / (size * (size - 1)) * 1.5);
  }
});
