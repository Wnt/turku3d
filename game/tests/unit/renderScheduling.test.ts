import { test, expect, vi } from 'vitest';
import { AdaptiveResolution } from '../../src/render/adaptiveResolution';
import { UploadBudget } from '../../src/render/uploadBudget';
import * as THREE from 'three/webgpu';
import { textureUploads } from '../../src/render/textureUploads';

function frames(controller: AdaptiveResolution, time: number, count: number, interval: number) {
  for (let i = 0; i < count; i++) controller.update(time += interval);
  return time;
}

test('resolution falls under sustained missed refreshes, respects floor and recovers slowly', () => {
  const controller = new AdaptiveResolution();
  let time = frames(controller, 0, 60, 1000 / 60);
  expect(controller.scale).toBe(1);
  time = frames(controller, time, 300, 1000 / 30);
  expect(controller.scale).toBe(0.5);
  time = frames(controller, time, 120, 1000 / 60);
  expect(controller.scale).toBe(0.5);
  time = frames(controller, time, 600, 1000 / 60);
  expect(controller.scale).toBeCloseTo(0.55);
  frames(controller, time, 12000, 1000 / 60);
  expect(controller.scale).toBe(1);
});

test('isolated load stalls and hidden-tab gaps do not lower resolution', () => {
  const controller = new AdaptiveResolution();
  let time = frames(controller, 0, 60, 1000 / 60);
  controller.update(time += 2000);
  time = frames(controller, time, 60, 1000 / 60);
  controller.update(time += 60000);
  frames(controller, time, 60, 1000 / 60);
  expect(controller.scale).toBe(1);
});

test('uploads prioritize largest jobs, bound bytes and run oversized textures alone', async () => {
  const budget = new UploadBudget(10, 2);
  const ran: number[] = [];
  const done = [3, 12, 4, 6].map(bytes => budget.enqueue(bytes, () => { ran.push(bytes); }));
  budget.drain(() => 0); expect(ran).toEqual([12]);
  budget.drain(() => 0); expect(ran).toEqual([12, 6, 4]);
  budget.drain(() => 0); expect(ran).toEqual([12, 6, 4, 3]);
  await Promise.all(done);
  expect(budget.pending).toBe(0);
});

test('elapsed upload budget yields after a slow job and propagates failures without stranding the queue', async () => {
  const budget = new UploadBudget(100, 2);
  let time = 0, second = false;
  const failed = budget.enqueue(10, () => { time += 3; throw new Error('device lost'); });
  const checked = expect(failed).rejects.toThrow('device lost');
  const done = budget.enqueue(5, () => { second = true; });
  budget.drain(() => time); expect(second).toBe(false);
  budget.drain(() => time);
  await checked; await done;
  expect(second).toBe(true);
});

test('glTF publication waits for uploads with final map settings and deduplicates shared maps', async () => {
  const callbacks: FrameRequestCallback[] = [];
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callbacks.push(callback); return callbacks.length; });
  try {
    const texture = new THREE.DataTexture(new Uint8Array(4), 1, 1);
    texture.colorSpace = THREE.SRGBColorSpace; texture.needsUpdate = true;
    const scene = new THREE.Scene();
    scene.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial({ map: texture })));
    const initTexture = vi.fn((map: THREE.Texture) => { expect(map.colorSpace).toBe(THREE.SRGBColorSpace); });
    const queue = textureUploads({ initTexture } as unknown as THREE.WebGPURenderer);
    let published = false;
    const done = queue.plugin().afterRoot!({ scene } as never)!.then(() => { published = true; });
    expect(initTexture).not.toHaveBeenCalled(); expect(published).toBe(false);
    callbacks.shift()!(0);
    await done;
    expect(published).toBe(true);
    await queue.enqueue(texture);
    expect(initTexture).toHaveBeenCalledTimes(1);
    texture.needsUpdate = true;
    const updated = queue.enqueue(texture);
    callbacks.shift()!(16);
    await updated;
    expect(initTexture).toHaveBeenCalledTimes(2);
  } finally { vi.unstubAllGlobals(); }
});
