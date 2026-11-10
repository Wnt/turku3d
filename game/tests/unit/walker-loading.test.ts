import { afterEach, expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { REGIONS } from '../../src/world/walkers/asset';
import { Walkers } from '../../src/world/walkers';

function emptyAsset() {
  const json = JSON.stringify({ format: 'turku3d-walkers', version: 3, fps: 30, atlasFormat: 'albedo-trim',
    vat: { offset: 0, length: 0, width: 0, height: 0, format: 'rgba16float', normal: { encoding: 'spherical-fibonacci', directions: 1 } },
    regions: REGIONS, variants: [] });
  const meta = new TextEncoder().encode(json.padEnd(Math.ceil(json.length / 4) * 4));
  const buffer = new ArrayBuffer(12 + meta.length);
  new Uint32Array(buffer, 0, 3).set([0x4b4c5754, 3, meta.length]);
  new Uint8Array(buffer, 12).set(meta);
  return buffer;
}
function crowd() {
  return new Walkers(new THREE.Group(), { cap: 10, now: () => 0, graphs: () => [], raycast: async () => null });
}
afterEach(() => vi.unstubAllGlobals());

test('a crowd can update before its explicitly requested low-priority asset arrives', async () => {
  const fetch = vi.fn(async () => new Response(emptyAsset())); vi.stubGlobal('fetch', fetch);
  const walkers = crowd();
  walkers.update(1 / 60, new THREE.PerspectiveCamera(), 0);
  expect(fetch).not.toHaveBeenCalled();
  expect(walkers.group.children).toHaveLength(0);
  await walkers.load('/walkers.bin');
  expect(fetch).toHaveBeenCalledWith('/walkers.bin', { priority: 'low' });
  expect(walkers.group.children).toHaveLength(1);
  walkers.dispose();
});

test('ending a session during background GPU preparation discards its staged crowd', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(emptyAsset())));
  const walkers = crowd(); let finish!: () => void;
  const prepare = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
  const loaded = walkers.load('/walkers.bin', prepare);
  await vi.waitFor(() => expect(prepare).toHaveBeenCalledOnce());
  walkers.dispose(); finish(); await loaded;
  expect(walkers.group.children).toHaveLength(0);
});

test('failed background GPU preparation cleans up and permits a later load', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(emptyAsset())));
  const walkers = crowd();
  await expect(walkers.load('/walkers.bin', async () => { throw Error('prepare'); })).rejects.toThrow('prepare');
  expect(walkers.group.children).toHaveLength(0);
  await walkers.load('/walkers.bin');
  expect(walkers.group.children).toHaveLength(1);
  walkers.dispose();
});
