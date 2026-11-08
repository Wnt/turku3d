import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { Environment } from '../../src/render/environment';
import { HAZE_DEPTH, MIST_DENSITY, mistAmount, type SceneFog } from '../../src/render/aerialFog';
import { WEATHER_PRESETS } from '../../src/render/weather/schedule';

test('the scene draws the aerial perspective node, which follows the timecycle, the weather and the sun', () => {
  const scene = new THREE.Scene();
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const env = new Environment(scene, renderer, 'medium');
  expect((scene as SceneFog).fogNode).toBe(env.aerial.node);
  // scene.fog still carries the fog colour and range for the passes that read them (rain, glow, id pass).
  expect(scene.fog).toBeInstanceOf(THREE.Fog);
  env.setSun(180, 45, 13);
  const clear = env.aerial.haze.value;
  expect(clear).toBeCloseTo(HAZE_DEPTH / env.sample.fogFar);
  expect(env.aerial.far.value).toBe(env.sample.fogFar);
  expect(env.aerial.color.value.toArray()).toEqual([...env.sample.fogColor]);
  expect(env.aerial.sunDirection.value.equals(env.sunDir)).toBe(true);
  expect(env.aerial.mist.value).toBe(0);
  // Weather fog shortens the visibility: denser haze and a full mist layer.
  env.setWeather(WEATHER_PRESETS.fog);
  expect(env.aerial.haze.value).toBeGreaterThan(clear * 20);
  expect(env.aerial.mist.value).toBeCloseTo(MIST_DENSITY);
  env.dispose();
});

test('low mist: weather fog and rain, and calm clear mornings around sunrise only', () => {
  const { clear, overcast, fog } = WEATHER_PRESETS;
  expect(mistAmount(clear, 2, 4.5)).toBeGreaterThan(0.2);
  expect(mistAmount(clear, 2, 19.5)).toBe(0); // the same sun in the evening
  expect(mistAmount(clear, 40, 9)).toBe(0);
  expect(mistAmount(clear, -20, 1)).toBe(0);
  expect(mistAmount({ ...clear, wind: 0.6 }, 2, 4.5)).toBe(0);
  expect(mistAmount(overcast, 2, 4.5)).toBeCloseTo(overcast.fog);
  expect(mistAmount(fog, 40, 13)).toBe(1);
  expect(mistAmount(WEATHER_PRESETS['heavy rain'], 40, 13)).toBe(0.5);
});
