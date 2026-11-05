import { afterEach, expect, it } from 'vitest';
import { CITY, CITIES, installCityFetch, onCityChange, setCity } from '../../src/core/city';
import { E0, N0, TRUE_NORTH_GRID_BEARING_DEG, enToEngine, engineToEN } from '../../src/core/origin';
import { CITY_LAT, sunPosition } from '../../src/core/sun';

afterEach(() => { setCity('turku'); });

it('a city switch rebases the engine origin, so the new city plays near (0, 0)', () => {
  expect(CITY.name).toBe('turku');
  const turku = enToEngine(CITIES.turku.spawn.E, CITIES.turku.spawn.N);
  setCity('espoo');
  expect(CITY.name).toBe('espoo');
  expect([E0, N0]).toEqual([CITIES.espoo.origin.E, CITIES.espoo.origin.N]);
  expect(TRUE_NORTH_GRID_BEARING_DEG).toBe(CITIES.espoo.trueNorthGridBearingDeg);
  const espoo = enToEngine(CITIES.espoo.spawn.E, CITIES.espoo.spawn.N);
  expect(Math.hypot(espoo.x, espoo.z)).toBeLessThan(1000);
  expect(engineToEN(espoo.x, 0, espoo.z)).toMatchObject({ E: CITIES.espoo.spawn.E, N: CITIES.espoo.spawn.N });
  setCity('turku');
  expect(enToEngine(CITIES.turku.spawn.E, CITIES.turku.spawn.N)).toEqual(turku);
});

it('listeners hear each switch once, after the origin moved; a switch to the active city is a no-op', () => {
  const heard: string[] = [];
  const off = onCityChange((city, previous) => heard.push(`${previous.name}>${city.name}:${E0}`));
  setCity('turku');
  setCity('amsterdam');
  setCity('rotterdam');
  off();
  setCity('turku');
  expect(heard).toEqual([`turku>amsterdam:${CITIES.amsterdam.origin.E}`, `amsterdam>rotterdam:${CITIES.rotterdam.origin.E}`]);
});

it('the sun follows the active city and tile fetches follow the switch', async () => {
  const seen: string[] = [];
  const target = { fetch: (async (input: RequestInfo | URL) => { seen.push(String(input)); return new Response('{}'); }) as typeof fetch,
    location: { origin: 'https://city.lab.madekivi.fi' } as Location };
  installCityFetch(target);
  await target.fetch('/coarse/health');
  const noon = new Date('2026-06-21T10:00:00Z');
  const turkuSun = sunPosition(noon);
  setCity('amsterdam');
  expect(CITY_LAT).toBe(CITIES.amsterdam.latLon.lat);
  expect(sunPosition(noon).elevation).not.toBeCloseTo(turkuSun.elevation, 1);
  await target.fetch('/coarse/health');
  setCity('turku');
  await target.fetch('/coarse/health');
  expect(seen).toEqual(['/coarse/health', 'https://city.lab.madekivi.fi/cities/amsterdam/coarse/health', '/coarse/health']);
});
