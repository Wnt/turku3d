import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { ScreenshotScenery } from '../../src/world/screenshotScenery';
import type { CoarseHost } from '../../src/world/coarseContracts';
const settle = async () => { for (let n = 0; n < 30; n++) await Promise.resolve(); };
function fixture() {
  const state = { available: false, generation: 'one', key: 'old', tile: '' };
  const scene = new THREE.Scene();
  const host = { scene } as CoarseHost;
  const request = vi.fn(async (url: string) => {
    if (url.includes('scenery?')) {
      state.tile ||= new URL(url, 'http://test').searchParams.get('tiles')!.split(',')[0];
      return new Response(JSON.stringify({ generation: state.generation,
        tiles: state.available ? [{ tile: state.tile, key: state.key, h: [0, 40], source_sha256: state.key }] : [],
        missing: state.available ? [] : [state.tile] }));
    }
    const key = url.split('/').at(-1)!.replace('.json', '');
    return new Response(JSON.stringify({ tile: state.tile, coarse: true, pass_kind: 'full',
      visual: { [state.tile]: { url: `../scenery/${key}.glb`, sha256: key } } }));
  });
  const load = vi.fn(async () => new THREE.Group());
  const dispose = vi.fn((model: THREE.Object3D) => model.removeFromParent());
  const scenery = new ScreenshotScenery(host, new Map(), request, load, () => {}, dispose, '/coarse/', 'http://test/');
  scenery.setRadius(4000, { x: 0, y: 5, z: 0, dx: 1, dz: 0, halfAngle: .5 });
  return { state, scene, scenery, request, load, dispose };
}
test('newly available scenery appears after the negative-cache TTL without resetting radius', async () => {
  const f = fixture(); await settle();
  f.scenery.update(0, 0, 1000); await settle(); expect(f.load).not.toHaveBeenCalled();
  f.state.available = true;
  f.scenery.update(0, 0, 29000); await settle(); expect(f.load).not.toHaveBeenCalled();
  f.scenery.update(0, 0, 30001); await settle();
  f.scenery.update(0, 0, 30017); await settle();
  expect(f.load).toHaveBeenCalledTimes(1); expect(f.scenery.progress.loaded).toBe(1);
});
test('generation refresh replaces a completed source and preserves the old model until replacement loads', async () => {
  const f = fixture(); await settle(); f.state.available = true;
  f.scenery.update(0, 0, 30001); await settle(); f.scenery.update(0, 0, 30017); await settle();
  const old = f.scene.children[0]; expect(old).toBeDefined();
  f.state.generation = 'two'; f.state.key = 'new';
  f.scenery.update(0, 0, 60002); await settle(); expect(old.parent).toBe(f.scene);
  f.scenery.update(0, 0, 60018); await settle();
  expect(f.load).toHaveBeenCalledWith('http://test/coarse/scenery/new.glb');
  expect(old.parent).toBeNull(); expect(f.scenery.progress.loaded).toBe(1);
});
test('late old-generation geometry cannot overwrite the refreshed source', async () => {
  const f = fixture(); await settle(); f.state.available = true;
  let release!: (model: THREE.Group) => void;
  f.load.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  f.scenery.update(0, 0, 30001); await settle(); f.scenery.update(0, 0, 30017); await settle();
  f.state.generation = 'two'; f.state.key = 'new';
  f.scenery.update(0, 0, 60002); await settle();
  const stale = new THREE.Group(); release(stale); await settle();
  expect(stale.parent).toBeNull(); expect(f.dispose).toHaveBeenCalledWith(stale, f.state.tile);
  f.scenery.update(0, 0, 60018); await settle();
  expect(f.load).toHaveBeenLastCalledWith('http://test/coarse/scenery/new.glb');
  expect(f.scenery.progress.loaded).toBe(1);
});


test('a new source clears an old failure while its replacement is preparing', async () => {
  const f = fixture(); await settle(); f.state.available = true;
  const request = f.request.getMockImplementation()!;
  f.request.mockImplementation(async url => url.includes('scenery?') ? request(url)
    : new Response('{}', { status: f.state.key === 'old' ? 503 : 202 }));
  f.scenery.update(0, 0, 30001); await settle(); f.scenery.update(0, 0, 30017); await settle();
  expect(f.scenery.progress.errors).toBe(1);
  f.state.key = 'new';
  f.scenery.update(0, 0, 60002); await settle();
  f.scenery.update(0, 0, 60018); await settle();
  expect(f.scenery.progress.errors).toBe(0);
  const requests = () => f.request.mock.calls.filter(([url]) => url.endsWith('/new.json')).length;
  expect(requests()).toBe(1);
  f.scenery.update(0, 0, 62019); await settle();
  expect(requests()).toBe(2);
});
