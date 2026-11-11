import { describe, expect, it } from 'vitest';
import { buildPreparedModel, readPreparedScene, type PreparedScene } from '../../src/world/preparedScene';

function scene(): PreparedScene {
  return { schema: 'turku-scene/1', release: 'release-1', id: 'office', origin: [23459500, 6704500, 0], bounds: [0, 0, 10, 10],
    sources: [], materials: [{ id: 'stone', color: [.2, .3, .4], roughness: .9, metalness: 0 }],
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
    const p = scene(); p.spawn.vehicle.position[0] = 11; expect(() => readPreparedScene(p)).toThrow('spawn outside');
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
