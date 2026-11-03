import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { AssetCache, sharedAssetKey, shareModelTextures, releaseModelTextures, releaseTextureData, releaseModelTextureData, keepTextureData,
  ktxTemplates, textureRelease } from '../../src/world/sharedAssets';
import { modelGpuBytes } from '../../src/world/gpuBytes';
import { LoadingKtx, guardReleasedTextures, sharedKtx } from '../../src/world/loadingKtx';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';

const url = `https://turku.lab.madekivi.fi/coarse/shared/${'a'.repeat(64)}.ktx2`;
test('content keys, in-flight dedupe, retry and byte LRU', async () => {
  expect(sharedAssetKey(url)).toBe(`${'a'.repeat(64)}.ktx2`);
  expect(sharedAssetKey(url.replace('/coarse/', '/coarse/_v/generation-one/'))).toBe(sharedAssetKey(url));
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
    const textures = await Promise.all(Array.from({ length: 22 }, (_, i) => loader.loadAsync(
      i % 2 ? url.replace('/coarse/', '/coarse/_v/generation-one/') : url)));
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


test('glTF dot segments preserve immutable identities across tile and generation paths', async () => {
  const hash = 'd'.repeat(64), key = `${hash}.ktx2`;
  const paths = [`https://example.com/coarse/_v/one/t0_1_0/../shared/${key}`, `/coarse/_v/two/t0_2_0/../shared/${key}?v=1`];
  for (const path of paths) expect(sharedAssetKey(path)).toBe(key);
  expect(sharedAssetKey(`/coarse/_v/one/t0_1_0/../shared/${hash}.bin`)).toBe(`${hash}.bin`);
  expect(sharedAssetKey(`blob:https://example.com/coarse/shared/${key}`)).toBeUndefined();
  expect(sharedAssetKey('/coarse/t0_1_0/../shared/mutable.ktx2')).toBeUndefined();
  const template = new THREE.CompressedTexture([{ data: new Uint8Array(8), width: 4, height: 4 }], 4, 4);
  const load = vi.spyOn(KTX2Loader.prototype, 'load').mockImplementation((_url, done) => { queueMicrotask(() => done?.(template)); });
  try {
    const loader = new LoadingKtx();
    const [first, second] = await Promise.all(paths.map(path => loader.loadAsync(path)));
    expect(load).toHaveBeenCalledOnce();
    expect(first.source).toBe(second.source);
    const a = model(first), b = model(second);
    shareModelTextures(a); shareModelTextures(b);
    expect(map(a)).toBe(map(b));
    releaseModelTextures(a, new Set([map(a)])); releaseModelTextures(b, new Set([map(b)]));
  } finally { load.mockRestore(); }
});

test('a resident compressed texture drops its CPU mips once uploaded; its template, other variants and kept maps keep theirs', async () => {
  const asset = url.replace('a'.repeat(64), 'b'.repeat(64));
  const template = new THREE.CompressedTexture([{ data: new Uint8Array(64), width: 8, height: 8 }, { data: new Uint8Array(16), width: 4, height: 4 }] as unknown as THREE.CompressedTextureMipmap[], 8, 8);
  const load = vi.spyOn(KTX2Loader.prototype, 'load').mockImplementation((_url, onLoad) => { queueMicrotask(() => onLoad?.(template)); });
  try {
    const loader = new LoadingKtx();
    const [first, second] = await Promise.all([loader.loadAsync(asset), loader.loadAsync(asset)]);
    second.offset.x = 0.5; // another glTF variant: its own resident texture
    const a = model(first), b = model(second);
    shareModelTextures(a); shareModelTextures(b);
    expect(ktxTemplates.get(map(a).source)).toBe(template);
    const before = modelGpuBytes(a), released = textureRelease.bytes;
    expect(releaseTextureData(map(a), () => false)).toBe(0); // not uploaded yet
    expect(releaseModelTextureData(a, () => true)).toBe(80);
    expect((map(a).mipmaps as unknown as { width: number; data: Uint8Array }[]).map(mip => [mip.width, mip.data.byteLength])).toEqual([[8, 0], [4, 0]]);
    expect(map(a).userData.releasedBytes).toBe(80);
    expect(textureRelease.bytes - released).toBe(80);
    expect(modelGpuBytes(a)).toBe(before); // the GPU copy keeps its size
    expect((template.mipmaps[0].data as Uint8Array).byteLength).toBe(64);
    expect((map(b).mipmaps as unknown as { data: Uint8Array }[])[0].data.byteLength).toBe(64);
    expect(ktxTemplates.has(map(a).source)).toBe(false); // a new variant decodes the source again
    expect(releaseTextureData(map(a), () => true)).toBe(0); // once
    keepTextureData(map(b));
    expect(releaseTextureData(map(b), () => true)).toBe(0); // surface array sources keep theirs
    const local = new THREE.CompressedTexture([{ data: new Uint8Array(8), width: 4, height: 4 }] as unknown as THREE.CompressedTextureMipmap[], 4, 4);
    expect(releaseTextureData(local, () => true)).toBe(0); // not a resident shared texture
    // A tile clone of the released texture resolves to the uploaded resident one before upload.
    const c = model(map(a).clone());
    shareModelTextures(c);
    expect(map(c)).toBe(map(a));
    for (const scene of [a, b, c]) releaseModelTextures(scene, new Set([map(scene)]));
  } finally { load.mockRestore(); }
});

test('released mips never reach an upload: the texture (or a clone) decodes again, keeps that data and uploads then', async () => {
  const asset = url.replace('a'.repeat(64), 'c'.repeat(64));
  const template = new THREE.CompressedTexture([{ data: new Uint8Array(64), width: 8, height: 8 }, { data: new Uint8Array(16), width: 4, height: 4 }] as unknown as THREE.CompressedTextureMipmap[], 8, 8);
  const load = vi.spyOn(KTX2Loader.prototype, 'load').mockImplementation((_url, onLoad) => { queueMicrotask(() => onLoad?.(template)); });
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  try {
    // The backend's upload of a compressed texture: what WebGPU's WriteTexture would get, per mip.
    const writes: number[][] = [];
    const renderer = { isWebGPURenderer: true, hasFeature: () => false,
      backend: { updateTexture: (texture: THREE.Texture, _options?: unknown) => { writes.push((texture.mipmaps as unknown as { data: Uint8Array }[]).map(mip => mip.data.byteLength)); } } };
    guardReleasedTextures(renderer as never);
    const scene = model(await sharedKtx(renderer as never).loadAsync(asset));
    shareModelTextures(scene);
    const resident = map(scene);
    expect(releaseTextureData(resident, () => true)).toBe(80);
    const clone = resident.clone(); // e.g. a placement's material clone made after the release
    renderer.backend.updateTexture(resident, {}); renderer.backend.updateTexture(clone, {});
    expect(writes).toEqual([]); // nothing uploads empty data
    const versions = [resident.version, clone.version];
    await vi.waitFor(() => expect([resident, clone].every(texture => texture.userData.keepData === true)).toBe(true));
    expect([resident.version, clone.version].map((v, i) => v > versions[i])).toEqual([true, true]); // uploads again
    renderer.backend.updateTexture(resident, {}); renderer.backend.updateTexture(clone, {});
    expect(writes).toEqual([[64, 16], [64, 16]]);
    expect(resident.userData.releasedBytes).toBeUndefined();
    expect(releaseTextureData(resident, () => true)).toBe(0); // keeps its data from now on
    releaseModelTextures(scene, new Set([resident]));
  } finally { load.mockRestore(); warn.mockRestore(); }
});
