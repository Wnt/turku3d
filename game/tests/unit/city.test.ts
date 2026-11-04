import { describe, expect, it } from 'vitest';
import { CITIES, cityFromSearch } from '../../src/core/city';

describe('city frame', () => {
  it('defaults to Turku with the historic origin and spawn', () => {
    const turku = cityFromSearch('');
    expect([turku.E0, turku.N0, turku.spawn.E, turku.spawn.N]).toEqual([23459500, 6704500, 23459668, 6704254]);
  });
  it('selects Espoo with ?city=espoo; origin on the 500 m block grid', () => {
    const espoo = cityFromSearch('?city=espoo');
    expect(espoo).toBe(CITIES.espoo);
    expect(espoo.E0 % 500).toBe(0);
    expect(espoo.N0 % 500).toBe(0);
    expect(espoo.spawn.E - espoo.E0).toBeGreaterThan(0);
  });
  it('rejects an unknown city', () => {
    expect(() => cityFromSearch('?city=nowhere')).toThrow();
  });
});
