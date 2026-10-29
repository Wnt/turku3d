import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { AssetCache, sharedAssetKey, shareModelTextures, releaseModelTextures } from '../../src/world/sharedAssets';
import { LoadingKtx } from '../../src/world/loadingKtx';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';

const url = `https://turku.lab.madekivi.fi/coarse/shared/${'a'.repeat(64)}.ktx2`;
test('content keys, in-flight dedupe, retry and byte LRU', async () => {
  expect(sharedAssetKey(url)).toBe(`${'a'.repeat(64)}.ktx2`);
  expect(sharedAssetKey(url.replace('/shared/', '/tile/'))).toBeUndefined();
  const cache = new AssetCache<Uint8Array>(2, bytes => bytes.length);
  const load = vi.fn(async () => new Uint8Array(1));
  await Promise.all(Array.from({ length: 22 }, () => cache.get('a', load)));
  expect(load).toHaveBeenCalledTimes(1);
  await cache.get('b', load); await cache.get('a', load); await cache.get('c', load);
  await cache.get('a', load); expect(load).toHaveBeenCalledTimes(3);
  await cache.get('b', load); expect(load).toHaveBeenCalledTimes(4);
  await expect(cache.get('error', async () => { throw new Error('retry'); })).rejects.toThrow('retry');
  await expect(cache.get('error', load)).resolves.toHaveLength(1);
});

test('shared KTX is fetched/transcoded once; glTF sampler mutations remain private', async () => {
  const template = new THREE.CompressedTexture([{ data: new Uint8Array(8), width: 4, height: 4 }], 4, 4);
  const load = vi.spyOn(KTX2Loader.prototype, 'load').mockImplementation((_url, onLoad) => { queueMicrotask(() => onLoad?.(template)); });
  try {
    const loader = new LoadingKtx();
    const textures = await Promise.all(Array.from({ length: 22 }, () => loader.loadAsync(url)));
    expect(load).toHaveBeenCalledTimes(1);
    expect(textures[0]).not.toBe(textures[1]);
    expect(textures[0].source).toBe(textures[1].source);
    textures[0].wrapS = THREE.RepeatWrapping;
    expect(textures[1].wrapS).toBe(THREE.ClampToEdgeWrapping);
  } finally { load.mockRestore(); }
});

function model(texture: THREE.Texture) {
  const scene = new THREE.Group();
  scene.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial({ map: texture })));
  return scene;
}
function map(scene: THREE.Group) { return ((scene.children[0] as THREE.Mesh).material as THREE.MeshStandardMaterial).map!; }
test('identical GPU textures survive either tile unloading; sampler/UV variants stay separate', () => {
  const texture = new THREE.Texture(); texture.userData.sharedAsset = 'test-residency';
  const a = model(texture), b = model(texture.clone());
  const offset = texture.clone(); offset.offset.x = .5;
  const c = model(offset);
  const rendered = offset.clone(); rendered.updateMatrix();
  const d = model(rendered);
  shareModelTextures(a); shareModelTextures(b); shareModelTextures(c); shareModelTextures(d);
  expect(map(a)).toBe(map(b)); expect(map(c)).not.toBe(map(a)); expect(map(c)).toBe(map(d));
  const dispose = vi.fn(); map(a).addEventListener('dispose', dispose);
  releaseModelTextures(a, new Set([map(a)]));
  expect(dispose).not.toHaveBeenCalled(); expect(map(b).source).toBe(texture.source);
  releaseModelTextures(b, new Set([map(b)]));
  expect(dispose).not.toHaveBeenCalled(); // Bounded idle cache preserves a quick revisit.
  releaseModelTextures(c, new Set([map(c)]));
  releaseModelTextures(d, new Set([map(d)]));
  const local = new THREE.Texture(), localDispose = vi.fn(); local.addEventListener('dispose', localDispose);
  releaseModelTextures(model(local), new Set([local])); expect(localDispose).toHaveBeenCalledOnce();
});


test('a resident decoded source survives eviction from the bounded template cache', async () => {
  const url = `https://turku.lab.madekivi.fi/coarse/shared/${'e'.repeat(64)}.ktx2`;
  const template = new THREE.CompressedTexture([{ data: new Uint8Array(65 * 1024 ** 2), width: 4, height: 4 }], 4, 4);
  const load = vi.spyOn(KTX2Loader.prototype, 'load').mockImplementation((_url, done) => { queueMicrotask(() => done?.(template)); });
  try {
    const loader = new LoadingKtx(), first = await loader.loadAsync(url), scene = model(first);
    shareModelTextures(scene);
    expect((await loader.loadAsync(url)).source).toBe(first.source);
    expect(load).toHaveBeenCalledTimes(1);
    const dispose = vi.fn(); map(scene).addEventListener('dispose', dispose);
    releaseModelTextures(scene, new Set([map(scene)]));
    expect(dispose).toHaveBeenCalledOnce(); // The now-idle oversize texture is evicted.
  } finally { load.mockRestore(); }
});
