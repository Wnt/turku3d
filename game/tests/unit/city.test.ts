import { describe, expect, it } from 'vitest';
import { CITIES, cityOf } from '../../src/core/city';

describe('city frame', () => {
  it('defaults to Turku with the historic origin and spawn', () => {
    const turku = cityOf('');
    expect([turku.origin.E, turku.origin.N, turku.spawn.E, turku.spawn.N]).toEqual([23459500, 6704500, 23459668, 6704254]);
  });
  it('selects Espoo with ?city=espoo; every origin is on the 500 m block grid', () => {
    expect(cityOf('?city=espoo')).toBe(CITIES.espoo);
    for (const city of Object.values(CITIES)) {
      expect(city.origin.E % 500, city.name).toBe(0);
      expect(city.origin.N % 500, city.name).toBe(0);
      expect(city.spawn.E - city.origin.E, city.name).toBeGreaterThan(0);
    }
  });
  it('rejects an unknown city', () => {
    expect(() => cityOf('?city=nowhere')).toThrow();
  });
});
