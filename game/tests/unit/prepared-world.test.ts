import { createHash } from 'node:crypto';
import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { writeCol } from '../../scripts/colWriter.mjs';
import { readCol } from '../../src/physics/colTile';
import { MAT } from '../../src/shared/col/materials';
import { PreparedWorld, type PreparedWorldHost } from '../../src/world/preparedWorld';
import { draftManifest } from '../../src/world/manifest';
import type { PreparedScene } from '../../src/world/preparedScene';
import { appearanceChanges, patchScene, readAppearancePatch } from '../../src/world/preparedAppearance';

function fixture() {
  const positions = [0, 2, 0, 0, 2, 62.5, 62.5, 4, 0];
  const bytes = writeCol({ i: 0, j: 0, positions: new Float32Array(positions), indices: new Uint32Array([0, 1, 2]),
    materials: new Uint8Array([MAT.road]), prims: [], flags: 1, meta: {} });
  const scene: PreparedScene = { schema: 'turku-scene/1', lightOccluders: [], id: 'test', release: 'a'.repeat(64), origin: [23459500, 6704500, 0], bounds: [0, 0, 62.5, 62.5],
    sources: [], assets: [], materials: [{ id: 'road', color: [.2, .2, .2], roughness: 1, metalness: 0 }], boundaries: [],
    supports: [{ id: 'road', kind: 'road', boundaryIds: [], meshId: 'ground' }],
    meshes: [{ id: 'ground', entityId: 'ground', materialId: 'road', positions, indices: [0, 1, 2], collision: 'solid', supportId: 'road' }],
    fixtures: [], emitters: [], coverage: { mode: 'replace', bounds: [0, 0, 62.5, 62.5], entityIds: ['ground'] },
    collision: { c_0_0: { url: 'ground.col', bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'), tris: 1, h: [2, 4] } },
    spawn: { position: [2, 2.064, 2], headingDeg: 0, vehicle: { position: [4, 2.128, 4], yaw: 0 } } };
  const manifest = draftManifest('https://city.test/');
  const host: PreparedWorldHost = { manifest, scene: new THREE.Scene(), renderer: {} as THREE.WebGPURenderer, pageUrl: 'https://city.test/',
    fetch: vi.fn(async input => String(input).endsWith('/regions.json') ? Response.json({ schema: 'turku-prepared-regions/1',
      defaultRegion: scene.id, regions: [{ id: scene.id, label: 'Test region', url: '/scenes/test.scene.json', release: scene.release, bounds: scene.bounds, spawn: scene.spawn }] })
      : new URL(String(input)).pathname.endsWith('.json') ? Response.json(scene) : new Response(new Uint8Array(bytes))),
    unloadCollision: vi.fn(), refreshCollision: vi.fn(), checkCollision: readCol, retainCollision: vi.fn(),
    prepareGraphics: async () => { expect(Object.keys(manifest.collision)).toHaveLength(0); } };
  return { scene, host };
}

test('prepared coverage uses authored bounds and actual collision support heights', async () => {
  const { host } = fixture(), world = await PreparedWorld.start(host);
  expect(world.groundBelow(30, 10)).toBeCloseTo(2.96);
  expect(world.streetsAt(30, 10)).toBe(true);
  expect(world.resolvedAt(60, 60)).toBe(true);
  expect(world.streetsAt(60, 60)).toBe(false); // Inside release bounds, but the fixture has no surface here.
  for (const [x, z] of [[-1, 2], [2, -1], [62.5, 2], [2, 62.5]]) {
    expect(world.resolvedAt(x, z)).toBe(false); expect(world.detailedAt(x, z)).toBe(false);
    expect(world.streetsAt(x, z)).toBe(false); expect(world.groundBelow(x, z)).toBeNaN();
    expect(world.viewStatus(x, z).ready).toBe(false);
  }
  expect(host.retainCollision).toHaveBeenCalledTimes(1);
  expect(Object.keys(host.manifest.collision)).toEqual(['c_0_0']);
  world.leaveCity();
  expect(world.resolvedAt(10, 10)).toBe(false); expect(world.groundBelow(10, 10)).toBeNaN();
  expect(host.scene.children).toHaveLength(0); expect(host.manifest.collision).toEqual({});
});

test('corrupt prepared collision never publishes a model or collision entry', async () => {
  const { host, scene } = fixture(); scene.collision.c_0_0.sha256 = 'a'.repeat(64);
  await expect(PreparedWorld.start(host)).rejects.toThrow('hash mismatch');
  expect(host.scene.children).toHaveLength(0); expect(host.manifest.collision).toEqual({});
  expect(host.retainCollision).not.toHaveBeenCalled();
});

test('explicit reference height selects a lower passage independently of its bridge deck', async () => {
  const { host, scene } = fixture(), lower = scene.meshes[0];
  const upper = { ...lower, id: 'bridge', entityId: 'bridge', supportId: null,
    positions: lower.positions.map((v, i) => i % 3 === 1 ? 8 : v) };
  scene.meshes.push(upper); scene.coverage.entityIds.push('bridge');
  const bytes = writeCol({ i: 0, j: 0, positions: new Float32Array([...lower.positions, ...upper.positions]), indices: new Uint32Array([0, 1, 2, 3, 4, 5]),
    materials: new Uint8Array([MAT.road, MAT.road]), prims: [], flags: 1, meta: {} });
  Object.assign(scene.collision.c_0_0, { bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'), tris: 2, h: [2, 8] });
  const fetcher = host.fetch!;
  host.fetch = (async input => String(input).endsWith('.json') ? fetcher(input) : new Response(new Uint8Array(bytes))) as typeof fetch;
  const world = await PreparedWorld.start(host);
  expect(world.groundBelow(10, 10)).toBeCloseTo(8);
  expect(world.groundBelow(10, 10, 4)).toBeCloseTo(2.32);
  expect(world.groundBelow(10, 10, 2)).toBeNaN();
  world.leaveCity();
});

test('prepared context receives the authored scenery recipe before generic styling', async () => {
  const { host, scene } = fixture(), asset = new THREE.Group();
  for (const layer of ['vegetation', 'buildings-lod-glazed']) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
    mesh.userData.layer = layer; asset.add(mesh);
  }
  const bytes = new Uint8Array([1, 2, 3]);
  scene.assets.push({ id: 'context', entityId: 'context', recipe: 'context', url: 'context.glb', position: [0, 0, 0], sha256: createHash('sha256').update(bytes).digest('hex') });
  const fetcher = host.fetch!;
  host.fetch = (async input => String(input).endsWith('.glb') ? new Response(bytes) : fetcher(input)) as typeof fetch;
  host.loadModel = async () => asset;
  host.style = async () => {
    for (const child of asset.children) {
      const mesh = child as THREE.Mesh;
      expect((mesh.material as THREE.Material).name).toBe(`distant-city-${mesh.userData.layer}`);
      expect(mesh.userData.scenery).toBe(true);
      expect(mesh.castShadow || mesh.receiveShadow).toBe(false);
    }
  };
  const world = await PreparedWorld.start(host); world.leaveCity();
});

test('partial startup releases successfully loaded assets and untracked preparation resources', async () => {
  const { host, scene } = fixture(), asset = new THREE.Group();
  const geometry = new THREE.BoxGeometry(), material = new THREE.MeshStandardMaterial();
  asset.add(new THREE.Mesh(geometry, material), new THREE.Mesh(geometry, material));
  const geometryDispose = vi.fn(), materialDispose = vi.fn(), drawDispose = vi.fn();
  geometry.addEventListener('dispose', geometryDispose); material.addEventListener('dispose', materialDispose);
  asset.children[0].addEventListener('dispose' as never, drawDispose);
  const bytes = new Uint8Array([1, 2, 3]);
  scene.assets.push({ id: 'asset', entityId: 'asset', recipe: 'building', url: 'building.glb', position: [0, 0, 0], sha256: createHash('sha256').update(bytes).digest('hex') });
  const fetcher = host.fetch!;
  host.fetch = (async input => String(input).endsWith('.glb') ? new Response(bytes) : fetcher(input)) as typeof fetch;
  host.loadModel = async () => asset; host.prepareGraphics = async () => { throw new Error('GPU preparation failed'); };
  host.unstyle = vi.fn(); host.releaseAsset = vi.fn();
  await expect(PreparedWorld.start(host)).rejects.toThrow('GPU preparation failed');
  expect(host.unstyle).toHaveBeenCalledOnce(); expect(host.releaseAsset).toHaveBeenCalledWith(asset, 'building');
  expect(geometryDispose).toHaveBeenCalledOnce(); expect(materialDispose).toHaveBeenCalledOnce(); expect(drawDispose).toHaveBeenCalledOnce();
  expect(host.scene.children).toHaveLength(0); expect(host.manifest.collision).toEqual({});
});

test('appearance update replaces materials and one asset while physics, collision and kept assets stay', async () => {
  const { host, scene } = fixture(), kept = new THREE.Group(), replaced = new THREE.Group(), next = new THREE.Group();
  const bytes = { kept: new Uint8Array([1]), old: new Uint8Array([2]), next: new Uint8Array([3]) };
  const sha = (b: Uint8Array) => createHash('sha256').update(b).digest('hex');
  scene.assets.push({ id: 'kept', entityId: 'kept', recipe: 'building', url: 'kept.glb', position: [0, 0, 0], sha256: sha(bytes.kept) },
    { id: 'roof', entityId: 'roof', recipe: 'building', url: 'old.glb', position: [1, 0, 0], sha256: sha(bytes.old) });
  const fetcher = host.fetch!, models: Record<string, THREE.Group> = { kept, old: replaced, next };
  const fetches: string[] = [];
  host.fetch = (async input => {
    const name = String(input).split('/').pop()!.replace('.glb', '');
    if (String(input).endsWith('.glb')) { fetches.push(name); return new Response(bytes[name as keyof typeof bytes]); }
    return fetcher(input);
  }) as typeof fetch;
  host.loadModel = async url => models[url.split('/').pop()!.replace('.glb', '')];
  host.releaseAsset = vi.fn(); host.unstyle = vi.fn(); host.trackModel = vi.fn(); host.untrackModel = vi.fn();
  const world = await PreparedWorld.start(host), first = host.scene.children[0];
  expect(fetches).toEqual(['kept', 'old']);
  host.prepareGraphics = vi.fn(async () => {}); // Startup's check that collision follows GPU preparation is not this contract.
  const patch = readAppearancePatch({ schema: 'turku-scene-appearance/1', scene: 'test',
    materials: [{ material: { id: 'road', color: [.5, .4, .3], roughness: .6, metalness: 0 } }],
    assets: [{ entityId: 'roof', url: 'next.glb', sha256: sha(bytes.next) }] });
  const report = await world.applyAppearance(patchScene(world.data, patch));
  expect(report).toMatchObject({ materials: ['road'], assets: ['roof'], uvMeshes: 0, reusedAssets: 1, loadedAssets: 1 });
  expect(fetches).toEqual(['kept', 'old', 'next']);
  expect(host.scene.children).toHaveLength(1); expect(host.scene.children[0]).not.toBe(first);
  expect(kept.parent).toBe(host.scene.children[0]); expect(next.parent).toBe(host.scene.children[0]); expect(replaced.parent).toBe(first); expect(first.parent).toBeNull();
  const road = host.scene.children[0].getObjectByName('prepared:road') as THREE.Mesh;
  expect((road.material as THREE.MeshStandardMaterial).roughness).toBe(.6);
  expect(host.untrackModel).toHaveBeenCalledWith(first, expect.any(String));
  expect(host.retainCollision).toHaveBeenCalledTimes(1); expect(host.unloadCollision).not.toHaveBeenCalled();
  expect(Object.keys(host.manifest.collision)).toEqual(['c_0_0']); expect(world.groundBelow(30, 10)).toBeCloseTo(2.96);
  expect(host.manifest.code).toBe(world.data.release); expect(world.entries.values().next().value!.model).toBe(host.scene.children[0]);
  // The same appearance again (a reload of what is drawn) keeps every GPU object.
  const drawn = host.scene.children[0];
  expect(await world.applyAppearance(world.data)).toMatchObject({ materials: [], assets: [], loadedAssets: 0, reusedAssets: 2 });
  expect(host.scene.children[0]).toBe(drawn); expect(fetches).toEqual(['kept', 'old', 'next']);
  world.leaveCity();
});

test('appearance update refuses geometry changes and pipeline-only UV rescaling', async () => {
  const { host } = fixture(), world = await PreparedWorld.start(host);
  const moved = { ...world.data, meshes: world.data.meshes.map(m => ({ ...m, positions: m.positions.map((v, i) => i === 1 ? v + 1 : v) })) };
  expect(() => appearanceChanges(world.data, moved)).toThrow('mesh ground differs');
  await expect(world.applyAppearance(moved)).rejects.toThrow('Not an appearance change');
  expect(() => patchScene(world.data, readAppearancePatch({ schema: 'turku-scene-appearance/1', scene: 'test',
    materials: [{ material: world.data.materials[0], uvScale: [.5, .5] }] }))).toThrow('python -m turku3d.scene.appearance');
  expect(() => patchScene(world.data, readAppearancePatch({ schema: 'turku-scene-appearance/1', scene: 'other' }))).toThrow('cannot change');
  expect(() => patchScene(world.data, readAppearancePatch({ schema: 'turku-scene-appearance/1', scene: 'test',
    materials: [{ material: { ...world.data.materials[0], id: 'marble' } }] }))).toThrow('unknown material');
  world.leaveCity();
});
