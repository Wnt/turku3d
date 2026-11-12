import { describe, expect, it, vi } from 'vitest';
import { createHash } from 'node:crypto';
import { buildPreparedModel, readPreparedScene, loadPreparedTextures, textureKey, type PreparedScene } from '../../src/world/preparedScene';

function scene(): PreparedScene {
  return { schema: 'turku-scene/1', release: 'release-1', id: 'office', origin: [23459500, 6704500, 0], bounds: [0, 0, 10, 10],
    sources: [], assets: [], materials: [{ id: 'stone', color: [.2, .3, .4], roughness: .9, metalness: 0 }],
    boundaries: [{ id: 'edge', positions: [[0, 0, 0], [10, 0, 0]] }],
    supports: [{ id: 'ground', kind: 'footway', boundaryIds: ['edge'], meshId: 'paving' }],
    meshes: [{ id: 'paving', entityId: 'street', materialId: 'stone', positions: [0, 0, 0, 0, 0, 10, 10, 0, 0], indices: [0, 1, 2],
      normals: [0, 1, 0, 0, 1, 0, 0, 1, 0], uv: [0, 0, 0, 10, 10, 0], collision: 'solid', supportId: 'ground' }], fixtures: [], emitters: [],
    coverage: { mode: 'replace', bounds: [0, 0, 10, 10], entityIds: ['street'] },
    collision: { c_0_0: { url: 'ground.col', sha256: 'a'.repeat(64), bytes: 100, tris: 1, h: [0, 0] } },
    spawn: { position: [2, 0, 2], headingDeg: 0, vehicle: { position: [4, 0, 4], yaw: 0 } } };
}

describe('prepared scene contract', () => {
  it('requires explicit collections and current schema', () => {
    const s = scene(); expect(readPreparedScene(s)).toBe(s);
    expect(() => readPreparedScene({ ...s, emitters: undefined })).toThrow('emitters');
    expect(() => readPreparedScene({ ...s, schema: 'turku-scene/0' })).toThrow('schema');
  });
  it('rejects broken support/material references and invalid geometry before upload', () => {
    for (const patch of [{ supportId: 'missing' }, { materialId: 'missing' }, { indices: [0, 1, 3] }, { positions: [NaN, 0, 0] }]) {
      const s = scene(); Object.assign(s.meshes[0], patch); expect(() => readPreparedScene(s)).toThrow('mesh');
    }
    const s = scene(); s.supports[0].boundaryIds = ['missing']; expect(() => readPreparedScene(s)).toThrow('support');
  });
  it('rejects missing collision integrity and spawns outside the owned bounds', () => {
    const s = scene(); s.collision.c_0_0.sha256 = ''; expect(() => readPreparedScene(s)).toThrow('collision');
    const p = scene(); p.spawn.vehicle.position[0] = 10; expect(() => readPreparedScene(p)).toThrow('spawn outside');
  });
  it('validates explicit shared edges on vertical faces without requiring a walkable support', () => {
    const s = scene(); s.meshes[0].supportId = null; s.meshes[0].boundaryIds = ['edge'];
    expect(readPreparedScene(s).meshes[0].boundaryIds).toEqual(['edge']);
    s.meshes[0].boundaryIds = ['missing'];
    expect(() => readPreparedScene(s)).toThrow('mesh boundaries paving');
  });
  it('uploads material batches with original indices, UV scale, normals, color and entity ownership', () => {
    const s = scene(); s.meshes.push({ ...s.meshes[0], id: 'paving2', entityId: 'street2' }); s.coverage.entityIds.push('street2');
    const root = buildPreparedModel(readPreparedScene(s));
    expect(root.children).toHaveLength(1);
    const mesh = root.children[0] as import('three/webgpu').Mesh;
    expect(Array.from(mesh.geometry.index!.array)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(mesh.geometry.getAttribute('uv').getY(1)).toBe(10);
    expect(mesh.geometry.getAttribute('normal').getY(0)).toBe(1);
    expect((mesh.material as import('three/webgpu').MeshStandardMaterial).color.r).toBeCloseTo(.2);
    expect(mesh.userData.preparedEntities.map((p: { entityId: string }) => p.entityId)).toEqual(['street', 'street2']);
    expect(mesh.castShadow && mesh.receiveShadow).toBe(true);
  });
});

it('loads shared hashed texture channels with correct color space and compiled UV orientation', async () => {
  const s = scene(), bytes = new Uint8Array([1, 2, 3]);
  const descriptor = { url: 'paving.png', sha256: createHash('sha256').update(bytes).digest('hex'), wrap: 'repeat' as const };
  s.materials[0].baseColorTexture = descriptor; s.materials[0].normalTexture = descriptor; s.materials[0].ormTexture = descriptor;
  const fetcher = vi.fn(async () => new Response(bytes)), close = vi.fn();
  const decode = vi.fn(async () => ({ width: 2, height: 2, close }) as ImageBitmap);
  const textures = await loadPreparedTextures(s, 'https://city.test/scenes/office.json', fetcher as typeof fetch, decode);
  expect(fetcher).toHaveBeenCalledTimes(2); // One sRGB resource and one shared linear normal/ORM resource.
  expect(textures.get(textureKey(descriptor, true))!.colorSpace).toBe('srgb');
  expect(textures.get(textureKey(descriptor, false))!.colorSpace).toBe('');
  expect([...textures.values()].every(t => !t.flipY)).toBe(true);
  const material = (buildPreparedModel(s, textures).children[0] as import('three/webgpu').Mesh).material as import('three/webgpu').MeshStandardMaterial;
  expect(material.map).not.toBe(material.normalMap);
  expect(material.normalMap).toBe(material.roughnessMap); expect(material.aoMap).toBe(material.metalnessMap);
});

it('rejects corrupt texture bytes before image decode', async () => {
  const s = scene(); s.materials[0].baseColorTexture = { url: 'corrupt.png', sha256: 'a'.repeat(64), wrap: 'clamp' };
  const decode = vi.fn();
  await expect(loadPreparedTextures(s, 'https://city.test/scene.json', (async () => new Response(new Uint8Array([1]))) as typeof fetch, decode)).rejects.toThrow('hash mismatch');
  expect(decode).not.toHaveBeenCalled();
});

it('keeps collision-only proxy meshes out of the visible scene and preserves prepared water planes', () => {
  const s = scene(); s.meshes[0].render = false; expect(buildPreparedModel(readPreparedScene(s)).children).toHaveLength(0);
  s.meshes[0].render = true; s.materials[0].water = { point: [0, 3, 0], normal: [0, 1, 0], band: 'aura' };
  const mesh = buildPreparedModel(readPreparedScene(s)).children[0] as import('three/webgpu').Mesh;
  expect(mesh.userData.waterSurface).toEqual({ point: [0, 3, 0], normal: [0, 1, 0] });
  expect(mesh.castShadow).toBe(false);
});
