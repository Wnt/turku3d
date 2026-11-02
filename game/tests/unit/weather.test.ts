import { describe, expect, it } from 'vitest';
import { FRONT_MS, WEATHER_NAMES, WEATHER_PRESETS, WeatherClock, weatherAt } from '../../src/render/weather/schedule';

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
});
