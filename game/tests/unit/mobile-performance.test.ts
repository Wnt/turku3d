import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { parseParams } from '../../src/core/params';
import { mobileDevice, renderPixelRatio } from '../../src/render/quality';
import { FramePacer } from '../../src/render/framePacer';
import { AdaptiveResolution } from '../../src/render/adaptiveResolution';
import { PostProcessing } from '../../src/render/post';
import { Environment } from '../../src/render/environment';
import { RiverMaterial } from '../../src/render/water';

const phone = { userAgent: 'Mozilla/5.0 (Linux; Android 16) Chrome/142 Mobile', maxTouchPoints: 5 };

test('phones default to sustainable rendering even with a controller; QA and explicit quality remain available', () => {
  expect(parseParams('', phone)).toMatchObject({ mobile: true, quality: 'mobile', aa: 'fxaa' });
  expect(parseParams('?quality=high&aa=taa', phone)).toMatchObject({ mobile: true, quality: 'high', aa: 'taa' });
  expect(parseParams('?cam=1,2,3,4,5,60', phone)).toMatchObject({ mobile: false, quality: 'medium', aa: 'taa' });
  expect(parseParams('')).toMatchObject({ mobile: false, quality: 'medium', aa: 'taa' });
  expect(mobileDevice('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Safari/605', 5)).toBe(true);
  expect(mobileDevice('Mozilla/5.0 (Windows NT 10.0) Chrome/142', 10)).toBe(false);
  expect(renderPixelRatio(832, 384, 3, false, true)).toBe(1.5);
  expect(renderPixelRatio(1920, 1080, 3, false, true)).toBeCloseTo(2 / 3);
  expect(renderPixelRatio(1920, 1080, 3, true, true)).toBe(1);
});

test.each([60, 90, 120, 144])('frame pacing delivers 30 updates per second on a %i Hz screen', hz => {
  const pacer = new FramePacer(30), frames: number[] = [];
  for (let n = 0; n < hz * 10; n++) {
    const time = n * 1000 / hz;
    if (pacer.ready(time)) frames.push(time);
  }
  expect(frames.length).toBeGreaterThanOrEqual(299);
  expect(frames.length).toBeLessThanOrEqual(301);
  expect(Math.min(...frames.slice(1).map((at, i) => at - frames[i]))).toBeGreaterThan(26);
});

test('late frames and a suspended tab never cause catch-up bursts', () => {
  const pacer = new FramePacer(30);
  expect(pacer.ready(0)).toBe(true);
  expect(pacer.ready(33.2)).toBe(true);
  expect(pacer.ready(95)).toBe(true);
  expect(pacer.ready(100)).toBe(false);
  expect(pacer.ready(10000)).toBe(true);
  expect(pacer.ready(10001)).toBe(false);
  expect(pacer.ready(10033.4)).toBe(true);
  const uncapped = new FramePacer(0);
  expect([0, 1, 2, 10000].every(t => uncapped.ready(t))).toBe(true);
});

test('30 FPS delivery does not collapse resolution, but sustained GPU cost does reserve headroom', () => {
  const controller = new AdaptiveResolution(30);
  let time = 0;
  const step = (count: number, cost: number, interval = 1000 / 30) => {
    for (let n = 0; n < count; n++) controller.update(time += interval, cost);
  };
  step(600, 12); expect(controller.scale).toBe(1);
  // Frames still arrive at 30 FPS, but 28 ms of work leaves almost no cooling/streaming headroom.
  step(210, 28); expect(controller.scale).toBe(0.5);
  step(600, 12); expect(controller.scale).toBe(0.5);
  step(400, 12); expect(controller.scale).toBeCloseTo(0.55);
  controller.update(time += 60000, 120);
  step(30, 12); expect(controller.scale).toBeCloseTo(0.55);
});

test('mobile pipeline compiles only beauty and allocates no normal/velocity prepass or planar river pass', async () => {
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  Object.assign(renderer, { _renderContexts: { get: () => ({}) } });
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera();
  const env = new Environment(scene, renderer, 'mobile');
  const post = new PostProcessing(renderer, scene, camera, 'mobile', 'fxaa');
  const water = new THREE.Mesh(new THREE.PlaneGeometry(10, 10).rotateX(-Math.PI / 2));
  const river = new RiverMaterial(water, env, 'mobile', false, renderer);
  const model = new THREE.Group(); model.add(new THREE.Mesh(new THREE.BoxGeometry())); scene.add(model);
  const compile = vi.spyOn(renderer, 'compileAsync').mockResolvedValue(undefined);
  try {
    expect(env.csm.cascades).toBe(1);
    expect(env.sun.shadow.mapSize.x).toBe(1024);
    await river.prepare(renderer, model, scene, camera);
    expect(compile).not.toHaveBeenCalled();
    expect(water.children).toHaveLength(0);
    expect(river.material.name).toBe('river-sky');
    await post.prepare(model);
    expect(compile).toHaveBeenCalledOnce();
    expect(renderer.getRenderTarget()).toBeNull();
  } finally { river.dispose(); post.dispose(); env.dispose(); vi.unstubAllGlobals(); }
});
