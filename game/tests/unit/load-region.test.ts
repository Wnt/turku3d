import { expect, test } from 'vitest';
import { TilesRenderer, type Tile } from '3d-tiles-renderer';
import { PlayerLoadRegion } from '../../src/world/loadRegion';

const tile = (id: string, children: Tile[] = []): Tile => ({ extras: { id }, geometricError: children.length ? 16 : 0,
  content: { uri: `${id}.glb` }, children, boundingVolume: { box: [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1] } } as unknown as Tile);

function fixture() {
  const children = [tile('t0_0_0'), tile('t0_1_0'), tile('t0_0_1'), tile('t0_1_1')];
  const parent = tile('t1_0_0', children), far = tile('t1_3_0');
  const root = { children: [parent, far], geometricError: 512 } as Tile;
  const renderer = new TilesRenderer();
  const region = new PlayerLoadRegion();
  renderer.registerPlugin(region);
  region.move(100, 100);
  renderer.dispatchEvent({ type: 'load-root-tileset', tileset: { root, asset: { version: '1.0' }, geometricError: 512 }, url: 'test' });
  return { renderer, region, children, parent, far };
}

test('indexes the entire tileset before first traversal and waits for every ring leaf, including offscreen ones', () => {
  const { renderer, region, children, far } = fixture();
  expect(region.progress).toEqual({ loaded: 0, total: 5 });
  expect(region.settled).toBe(false); // loadProgress === 1 alone is insufficient
  for (const child of children) renderer.dispatchEvent({ type: 'load-model', tile: child, scene: {} as never, url: '' });
  expect(region.progress.loaded).toBe(4);
  expect(region.settled).toBe(false);
  renderer.dispatchEvent({ type: 'load-model', tile: far, scene: {} as never, url: '' });
  expect(region.settled).toBe(true);
  renderer.dispatchEvent({ type: 'dispose-model', tile: children[0], scene: {} as never });
  expect(region.settled).toBe(false);
});

test('region masks frustum decisions and refines only the selected parents', () => {
  const { region, parent, far } = fixture();
  const target = { inView: false, error: 0, distance: Infinity };
  region.calculateTileViewError(parent, target);
  expect(target.inView).toBe(true);
  expect(target.error).toBeGreaterThan(8);
  region.calculateTileViewError(far, target);
  expect(target).toEqual({ inView: true, error: 0, distance: 1400 });
  region.calculateTileViewError(tile('t1_10_0'), target);
  expect(target.inView).toBe(false);
});

test('cache admission always has headroom above resident ring bytes, then shrinks after moving away', () => {
  const { renderer, region, children } = fixture();
  expect(renderer.lruCache.unloadPriorityCallback).toBeTypeOf('function');
  const bytes = 600 * 1024 ** 2;
  renderer.lruCache.add(children[0], () => {});
  renderer.lruCache.setMemoryUsage(children[0], bytes);
  region.move(100, 100);
  expect(renderer.lruCache.maxBytesSize).toBeGreaterThan(bytes);
  expect(renderer.lruCache.isFull()).toBe(false);
  region.move(5000, 5000);
  expect(renderer.lruCache.maxBytesSize).toBeLessThan(bytes);
});

test('retains a distant menu candidate through focus changes, then releases its visual region', () => {
  const { region, far } = fixture();
  const target = { inView: false, error: 0, distance: 0 };
  region.retain([{ x: 1500, z: 100 }]);
  region.move(5000, 5000);
  region.calculateTileViewError(far, target);
  expect(target.inView).toBe(true);
  region.retain([]);
  region.calculateTileViewError(far, target);
  expect(target.inView).toBe(false);
});
