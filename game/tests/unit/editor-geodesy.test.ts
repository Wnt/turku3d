import { expect, test } from 'vitest';
import { framesOf, gk23ToTm35, tm35AzimuthOffsetDeg } from '../../src/dev/editor/geodesy';

// pyproj 3.x Transformer.from_crs(3877, 3067, always_xy=True)
const REFERENCE: [number, number, number, number][] = [
  [23459668, 6704254, 239751.0006, 6710706.2376],
  [23459500, 6704500, 239598.1886, 6710962.0919],
  [23455000, 6710000, 235438.8750, 6716727.7430],
  [23465000, 6698000, 244695.4085, 6704137.4323],
];

test('EPSG:3877 -> EPSG:3067 matches pyproj to a millimetre', () => {
  for (const [e, n, te, tn] of REFERENCE) {
    const t = gk23ToTm35(e, n);
    expect(Math.abs(t.E - te)).toBeLessThan(0.001);
    expect(Math.abs(t.N - tn)).toBeLessThan(0.001);
  }
});

test('engine frames carry all three coordinate systems; grid azimuths differ by pyproj 3.4819 degrees', () => {
  const f = framesOf(168, 4.2, 246);
  expect(f.epsg3877).toEqual({ E: 23459668, N: 6704254, H: 4.2 });
  expect(Math.abs(f.epsg3067.E - 239751.001)).toBeLessThan(0.002);
  const offset = tm35AzimuthOffsetDeg(23459668, 6704254);
  expect(Math.abs(offset - 3.481928)).toBeLessThan(1e-4);
});
