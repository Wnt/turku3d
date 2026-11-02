import { expect, test } from 'vitest';
import { cloudPixels } from '../../src/render/cloudField';

test('wind-driven cloud maps tile without a cirrus edge discontinuity', () => {
  const size = 512, pixels = cloudPixels(size);
  // Compare wrap-edge differences with ordinary adjacent pixels. This catches a rectangular
  // cirrus field accidentally wrapping both axes at its longer (32-cell) period.
  for (const channel of [0, 1, 2]) for (const axis of [0, 1]) {
    let edge = 0, interior = 0;
    const at = (a: number, b: number) => pixels[((axis ? a : b) * size + (axis ? b : a)) * 4 + channel];
    for (let b = 0; b < size; b++) {
      edge += Math.abs(at(0, b) - at(size - 1, b));
      for (let a = 1; a < size; a++) interior += Math.abs(at(a, b) - at(a - 1, b));
    }
    expect(edge / size).toBeLessThan(interior / (size * (size - 1)) * 3 + 0.5);
  }
});
