import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { Environment } from '../../src/render/environment';

test('city clipping covers high-tier outer nodes while preserving weather and vegetation settings', () => {
  const scene = new THREE.Scene();
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const env = new Environment(scene, renderer, 'medium');
  const camera = new THREE.PerspectiveCamera(60, 1, .1, 6000);
  env.setSun(180, 45, 13);
  const fog = (scene.fog as THREE.Fog).clone();
  for (const radius of [4000, 8000, 15000]) {
    env.setCityDistance(camera, radius);
    const nodeExtent = radius + Math.SQRT2 * (radius > 4000 ? 4000 : 1000);
    expect(camera.far).toBeGreaterThan(nodeExtent);
    expect(env.sky.scale.x / 2).toBeGreaterThan(nodeExtent);
    expect(camera.far).toBeGreaterThan(env.sky.scale.x / 2 * Math.sqrt(3));
    expect(env.screenshotDistance).toBe(0);
    expect((scene.fog as THREE.Fog).near).toBe(fog.near);
    expect((scene.fog as THREE.Fog).far).toBe(fog.far);
    expect((scene.fog as THREE.Fog).color.equals(fog.color)).toBe(true);
  }
  env.setScreenshotDistance(camera, 15000);
  expect(env.screenshotDistance).toBe(15000);
  env.setScreenshotDistance(camera, 0);
  expect(camera.far).toBe(6000); expect(env.sky.scale.x).toBe(4500);
  env.dispose();
});
