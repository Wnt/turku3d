import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { Environment } from '../../src/render/environment';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { timecycle } from '../../src/render/timecycle';
import { weatherLighting } from '../../src/render/weather/lighting';
import { WEATHER_PRESETS } from '../../src/render/weather/schedule';

test('clear and broken cloud preserve the calibrated timecycle exactly', () => {
  for (const elevation of [-20, -3, 4, 12, 35]) {
    const base = timecycle(elevation);
    expect(weatherLighting(base, WEATHER_PRESETS.clear)).toBe(base);
    expect(weatherLighting(base, WEATHER_PRESETS['partly cloudy'])).toBe(base);
  }
});

test('repeated weather updates and time edits never compound light or fog multipliers', () => {
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const env = new Environment(new THREE.Scene(), renderer, 'medium');
  try {
    env.setSun(180, 35, 13);
    env.setWeather(WEATHER_PRESETS.overcast);
    const cloudy = structuredClone(env.sample);
    expect(env.atmosphere.weatherHorizon.value.toArray()).toEqual(cloudy.fogColor);
    for (let n = 0; n < 100; n++) env.setWeather({ ...WEATHER_PRESETS.overcast });
    expect(env.sample).toEqual(cloudy);
    expect(env.atmosphere.weatherHorizon.value.toArray()).toEqual(cloudy.fogColor);
    expect(env.sun.intensity).toBe(cloudy.sunIntensity);
    expect(renderer.toneMappingExposure).toBe(cloudy.exposure);
    env.setSun(250, 4, 19.5);
    expect(env.sample).toEqual(weatherLighting(timecycle(4), WEATHER_PRESETS.overcast));
    expect(env.atmosphere.weatherHorizon.value.toArray()).toEqual(env.sample.fogColor);
    env.setWeather(WEATHER_PRESETS.clear);
    expect(env.sample).toEqual(timecycle(4));
    env.setSun(180, 35, 13);
    expect(env.sample).toEqual(timecycle(35));
  } finally { env.dispose(); }
});

test('overcast replaces directional light with diffuse fill, while night light levels stay calibrated', () => {
  const day = timecycle(35), cloudy = weatherLighting(day, WEATHER_PRESETS.overcast);
  expect(cloudy.sunIntensity).toBeLessThan(day.sunIntensity * .3);
  expect(cloudy.hemisphereIntensity).toBeGreaterThan(day.hemisphereIntensity * 2);
  expect(cloudy.environmentIntensity).toBeGreaterThan(day.environmentIntensity);
  const night = timecycle(-20), rainyNight = weatherLighting(night, WEATHER_PRESETS['heavy rain']);
  for (const key of ['sunIntensity', 'hemisphereIntensity', 'environmentIntensity', 'exposure', 'night', 'bloomStrength'] as const)
    expect(rainyNight[key]).toBe(night[key]);
  expect(rainyNight.fogColor).toEqual(night.fogColor);
});

test('dense fog remains dense when the clear-weather view distance grows', () => {
  const base = timecycle(35), original = structuredClone(base);
  const short = weatherLighting(base, WEATHER_PRESETS.fog);
  const long = weatherLighting({ ...base, fogFar: 15_000 }, WEATHER_PRESETS.fog);
  expect(short.fogFar).toBeGreaterThan(150);
  expect(long.fogFar).toBeLessThan(220);
  expect(long.fogFar - short.fogFar).toBeLessThan(15);
  expect(short.fogNear).toBeLessThan(short.fogFar);
  expect(base).toEqual(original);
});
