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
