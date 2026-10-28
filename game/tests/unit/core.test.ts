import { test, expect } from 'vitest';
import { enToEngine, engineToEN, collisionTileAt, headingToYaw, yawToHeading, OFFICE_EN } from '../../src/core/origin';
import { parseParams } from '../../src/core/params';
import { sunPosition } from '../../src/core/sun';

test('origin round trip and office tile', () => {
  const p = enToEngine(OFFICE_EN.E, OFFICE_EN.N, 3.2);
  expect(p).toEqual({ x: 168, y: 3.2, z: 246 });
  expect(engineToEN(p.x, p.y, p.z)).toEqual({ E: OFFICE_EN.E, N: OFFICE_EN.N, H: 3.2 });
  expect(collisionTileAt(p.x, p.z)).toEqual({ i: 2, j: 3 });
  expect(collisionTileAt(-0.1, 62.5)).toEqual({ i: -1, j: 1 });
});
test('heading <-> yaw', () => {
  for (const h of [0, 45, 90, 180, 270, 359]) expect(yawToHeading(headingToYaw(h))).toBeCloseTo(h, 6);
});
test('url params', () => {
  expect(parseParams('').aa).toBe('taa');
  for (const aa of ['off', 'taa', 'fxaa']) expect(parseParams(`?aa=${aa}`).aa).toBe(aa);
  expect(() => parseParams('?aa=unknown')).toThrow(/aa/);
  const p = parseParams('?cam=23459668,6704254,4.5,90,-5,70&time=2026-07-01T10:00:00Z&backend=webgl');
  expect(p.backend).toBe('webgl');
  expect(p.qa).toEqual({ E: 23459668, N: 6704254, H: 4.5, yawDeg: 90, pitchDeg: -5, fovDeg: 70, rollDeg: 0 });
  expect(p.pass).toBe('color');
  expect(p.time.toISOString()).toBe('2026-07-01T10:00:00.000Z');
  expect(p.manifest).toBe('../tiles/manifest.json');
  expect(parseParams('?tiles=/tiles/tileset.json').manifest).toBe('/tiles/manifest.json');
  expect(parseParams('?tiles=../tiles/tileset.json').manifest).toBe('../tiles/manifest.json');
  expect(parseParams('?tiles=http://tiles.example/tiles/tileset.json').manifest).toBe('http://tiles.example/tiles/manifest.json');
  expect(() => parseParams('?colRadius=abc')).toThrow(/colRadius/);
  expect(() => parseParams('?cam=1,2,3')).toThrow();
  // qa/README.md §2 contract: roll as its own param (or 7th cam value), pass=id, qa=1 accepted
  const q = parseParams('?cam=23459675.963,6704242.703,5.622,324.819,15,70&roll=-2.5&qa=1&pass=id&time=2019-06-15T14:00:00%2B03:00');
  expect(q.qa).toEqual({ E: 23459675.963, N: 6704242.703, H: 5.622, yawDeg: 324.819, pitchDeg: 15, fovDeg: 70, rollDeg: -2.5 });
  expect(q.pass).toBe('id');
  expect(q.time.toISOString()).toBe('2019-06-15T11:00:00.000Z');
  expect(parseParams('?cam=1,2,3,4,5,60,7').qa!.rollDeg).toBe(7);
  expect(() => parseParams('?cam=1,2,3,4,5,0')).toThrow();
  expect(() => parseParams('?cam=1,2,3,4,5,60&pass=depth')).toThrow();
});
test('sun position in Turku', () => {
  // solar noon in Turku ~ 12:30 local (09:30 UTC) at midsummer: az ~180, el ~ 90-60.45+23.44 = 53
  const noon = sunPosition(new Date('2026-06-21T10:30:00Z'));
  expect(noon.azimuth).toBeGreaterThan(165); expect(noon.azimuth).toBeLessThan(195);
  expect(noon.elevation).toBeGreaterThan(51); expect(noon.elevation).toBeLessThan(54);
  const winter = sunPosition(new Date('2026-12-21T10:30:00Z'));
  expect(winter.elevation).toBeGreaterThan(4); expect(winter.elevation).toBeLessThan(8);
  expect(sunPosition(new Date('2026-12-21T22:00:00Z')).elevation).toBeLessThan(0);
});

test('sun direction is rotated from true to grid azimuth (meridian convergence)', async () => {
  const { sunDirection } = await import('../../src/core/sun');
  const { TRUE_NORTH_GRID_BEARING_DEG } = await import('../../src/core/origin');
  const d = sunDirection(new Date('2026-06-21T10:30:00Z'));
  const gridAz = ((Math.atan2(d.x, -d.z) * 180) / Math.PI + 360) % 360; // engine: east +x, north -z
  expect(gridAz - d.azimuth).toBeCloseTo(TRUE_NORTH_GRID_BEARING_DEG, 6);
  expect(Math.hypot(d.x, d.y, d.z)).toBeCloseTo(1, 9);
});

test('Turku is the deployment-relative default; development requires an explicit scene', () => {
  const defaults = parseParams('');
  expect(defaults.tileset).toBe('../tiles/tileset.json');
  expect(defaults.spawn).toMatchObject(OFFICE_EN);
  for (const [page, tiles] of [
    ['https://wnt.github.io/turku-game/game/', 'https://wnt.github.io/turku-game/tiles/'],
    ['http://localhost:5220/', 'http://localhost:5220/tiles/'],
    ['http://localhost:5221/', 'http://localhost:5221/tiles/'],
  ]) {
    expect(new URL(defaults.tileset, page).href).toBe(`${tiles}tileset.json`);
    expect(new URL(defaults.manifest, page).href).toBe(`${tiles}manifest.json`);
  }
  expect(parseParams('?scene=dev').tileset).toBe('dev-tiles/tileset.json');
  expect(parseParams('?scene=dev').manifest).toBe('dev-tiles/manifest.json');
  expect(parseParams('?scene=unknown').tileset).toBe(defaults.tileset);
  const override = parseParams('?scene=dev&tiles=qa/tileset.json&backend=webgl');
  expect(override.tileset).toBe('qa/tileset.json');
  expect(override.manifest).toBe('qa/manifest.json');
  expect(override.backend).toBe('webgl');
});
