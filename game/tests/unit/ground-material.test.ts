import { describe, it, expect } from 'vitest';
import * as THREE from 'three/webgpu';
import { GROUND_KEYS, validateGroundSpec, createGroundMaterial } from '../../src/render/ground/material';
import type { GroundTileSpec } from '../../src/render/ground/material';

const spec = (): GroundTileSpec => ({ version: 1, material_keys: [...GROUND_KEYS],
  bounds_xz: [0, 0, 250, 250], ortho_uv: { offset: 65 / 6380, scale: 6250 / 6380 },
  splat_uv: { offset: 8 / 1040, scale: 1024 / 1040 },
  textures: { ortho: { uri: 'ortho.ktx2' }, splat0: { uri: 'splat0.ktx2' }, splat1: { uri: 'splat1.ktx2' } } });

describe('ground material contract', () => {
  it('rejects mismatched library channel order before sampling the wrong surface', () => {
    const s = spec(); expect(() => validateGroundSpec(s)).not.toThrow();
    s.material_keys.reverse(); expect(() => validateGroundSpec(s)).toThrow(/layer order/);
  });
  it('rejects invalid bounds and gutter mappings', () => {
    const s = spec(); s.bounds_xz[2] = 0;
    expect(() => validateGroundSpec(s)).toThrow(/bounds/);
    const t = spec(); t.ortho_uv.scale = 1;
    expect(() => validateGroundSpec(t)).toThrow(/gutter/);
  });
});

it('new ground tiles reuse resident library arrays without invalidating their GPU contents', () => {
  const texture = () => new THREE.Texture();
  const library = { albedo: texture(), normal: texture(), orm: texture() };
  const maps = { ortho: texture(), splat0: texture(), splat1: texture() };
  const first = createGroundMaterial(spec(), maps, library);
  const versions = Object.values(library).map(map => map.version);
  expect(versions.every(version => version > 0)).toBe(true);
  const second = createGroundMaterial(spec(), maps, library);
  expect(Object.values(library).map(map => map.version)).toEqual(versions);
  library.albedo.anisotropy = 1;
  const corrected = createGroundMaterial(spec(), maps, library);
  expect(library.albedo.anisotropy).toBe(4);
  expect(library.albedo.version).toBe(versions[0] + 1);
  expect(library.normal.version).toBe(versions[1]);
  first.dispose(); second.dispose(); corrected.dispose();
});
