import { describe, it, expect } from 'vitest';
import { GROUND_KEYS, validateGroundSpec } from '../../src/render/ground/material';
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
