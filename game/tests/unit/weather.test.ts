import { describe, expect, it } from 'vitest';
import { FRONT_MS, FRONT_SHARES, WEATHER_NAMES, WEATHER_PRESETS, WeatherClock, frontName, weatherAt } from '../../src/render/weather/schedule';

describe('shared weather', () => {
  it('joins with the same state at any point without simulation history', () => {
    for (let now = 0; now < 100 * FRONT_MS; now += 71371) {
      const a = new WeatherClock(() => now), b = new WeatherClock(() => now);
      for (let i = 0; i < 5; i++) a.update();
      expect(a.sample).toEqual(b.update());
      for (const value of Object.values(a.sample)) expect(value).toBeGreaterThanOrEqual(0);
      for (const value of Object.values(a.sample)) expect(value).toBeLessThanOrEqual(1);
    }
  });
  it('has no discontinuity at front boundaries, including wetness', () => {
    for (let i = -100; i < 100; i++) {
      const before = weatherAt(i * FRONT_MS - 1), after = weatherAt(i * FRONT_MS + 1);
      for (const k of Object.keys(before) as (keyof typeof before)[]) expect(Math.abs(before[k] - after[k])).toBeLessThan(.0001);
    }
  });
  it('smoothly interrupts a local transition and returns to the shared schedule', () => {
    let now = 1700000000000;
    const clock = new WeatherClock(() => now, undefined, 'clear');
    clock.setWeather('heavy rain', 10);
    now += 5000;
    expect(clock.update().rain).toBeCloseTo(.5);
    const middle = { ...clock.sample };
    clock.setWeather('fog', 10);
    expect(clock.sample).toEqual(middle);
    now += 10000;
    expect(clock.update()).toEqual(WEATHER_PRESETS.fog);
    clock.setWeather('live', 2);
    now += 2000;
    expect(clock.update()).toEqual(weatherAt(now));
  });
  it('exposes all six presets and rejects invalid control values', () => {
    const clock = new WeatherClock(() => 0);
    for (const name of WEATHER_NAMES) { clock.setWeather(name, 0); expect(clock.sample).toEqual(WEATHER_PRESETS[name]); }
    expect(() => clock.setWeather('clear', NaN)).toThrow();
  });
  it('makes partly cloudy the usual front and the grey ones rare', () => {
    expect(Object.values(FRONT_SHARES).reduce((a, b) => a + b, 0)).toBe(100);
    const counts = Object.fromEntries(WEATHER_NAMES.map(n => [n, 0])) as Record<string, number>;
    // Fronts of the present era (2026), the indices players actually meet.
    const first = Math.floor(Date.UTC(2026, 0, 1) / FRONT_MS), n = 20000;
    for (let i = first; i < first + n; i++) counts[frontName(i)]++;
    const share = (name: string) => counts[name] / n;
    for (const name of WEATHER_NAMES) expect(Math.abs(share(name) - FRONT_SHARES[name] / 100)).toBeLessThan(.02);
    expect(share('partly cloudy')).toBeGreaterThan(Math.max(...WEATHER_NAMES.filter(x => x !== 'partly cloudy').map(share)) + .2);
    expect(share('overcast')).toBeLessThan(.08);
    expect(share('overcast') + share('light rain') + share('heavy rain') + share('fog')).toBeLessThan(.25);
  });
});
