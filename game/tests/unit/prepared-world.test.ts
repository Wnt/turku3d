import { createHash } from 'node:crypto';
import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { writeCol } from '../../scripts/colWriter.mjs';
import { readCol } from '../../src/physics/colTile';
import { MAT } from '../../src/shared/col/materials';
import { PreparedWorld, type PreparedWorldHost } from '../../src/world/preparedWorld';
import { draftManifest } from '../../src/world/manifest';
import type { PreparedScene } from '../../src/world/preparedScene';

function fixture() {
  const positions = [0, 2, 0, 0, 2, 62.5, 62.5, 4, 0];
  const bytes = writeCol({ i: 0, j: 0, positions: new Float32Array(positions), indices: new Uint32Array([0, 1, 2]),
    materials: new Uint8Array([MAT.road]), prims: [], flags: 1, meta: {} });
  const scene: PreparedScene = { schema: 'turku-scene/1', id: 'test', release: 'test', origin: [23459500, 6704500, 0], bounds: [0, 0, 62.5, 62.5],
    sources: [], assets: [], materials: [{ id: 'road', color: [.2, .2, .2], roughness: 1, metalness: 0 }], boundaries: [],
    supports: [{ id: 'road', kind: 'road', boundaryIds: [], meshId: 'ground' }],
    meshes: [{ id: 'ground', entityId: 'ground', materialId: 'road', positions, indices: [0, 1, 2], collision: 'solid', supportId: 'road' }],
    fixtures: [], emitters: [], coverage: { mode: 'replace', bounds: [0, 0, 62.5, 62.5], entityIds: ['ground'] },
    collision: { c_0_0: { url: 'ground.col', bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'), tris: 1, h: [2, 4] } },
    spawn: { position: [2, 2.064, 2], headingDeg: 0, vehicle: { position: [4, 2.128, 4], yaw: 0 } } };
  const manifest = draftManifest('https://city.test/');
  const host: PreparedWorldHost = { manifest, scene: new THREE.Scene(), renderer: {} as THREE.WebGPURenderer, pageUrl: 'https://city.test/',
    fetch: vi.fn(async input => new URL(String(input)).pathname.endsWith('.json') ? Response.json(scene) : new Response(new Uint8Array(bytes))),
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
