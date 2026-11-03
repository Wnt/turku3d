import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { sampleMoved, timecycle } from '../../src/render/timecycle';
import { Environment } from '../../src/render/environment';

test('a sample moves only by more than 0.2 % of a value, on any scalar or colour channel', () => {
  const a = timecycle(20);
  expect(sampleMoved(a, timecycle(20))).toBe(false);
  expect(sampleMoved(a, { ...a, environmentIntensity: a.environmentIntensity * 1.001 })).toBe(false);
  expect(sampleMoved(a, { ...a, environmentIntensity: a.environmentIntensity * 1.003 })).toBe(true);
  expect(sampleMoved(a, { ...a, sunColor: [a.sunColor[0], a.sunColor[1] * 1.01, a.sunColor[2]] })).toBe(true);
  // Values near zero (night ramps) still follow small absolute changes.
  expect(sampleMoved({ ...a, night: 0 }, { ...a, night: 0.001 })).toBe(true);
});

test('the sun follows every frame; lighting uniforms change only when the timecycle moved visibly', () => {
  const scene = new THREE.Scene();
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const env = new Environment(scene, renderer, 'medium');
  env.setSun(180, 20, 12);
  const intensity = scene.environmentIntensity, colour = env.sun.color.clone(), direction = env.sunDir.clone();
  // One frame of the 36x day: thousandths of a degree.
  env.setSun(180.004, 20.002, 12.0001);
  expect(env.sunDir.equals(direction)).toBe(false);
  expect(scene.environmentIntensity).toBe(intensity);
  expect(env.sun.color.equals(colour)).toBe(true);
  // Minutes later the light has visibly changed and is applied.
  env.setSun(185, 12, 13);
  expect(sampleMoved(timecycle(20), timecycle(12))).toBe(true);
  expect(scene.environmentIntensity !== intensity || !env.sun.color.equals(colour)).toBe(true);
});
