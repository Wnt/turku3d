import { describe, expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { LampField, type Lamp } from '../../src/render/lights/lampField';
import { FLOOD, FloodMasts, decodeFloodlights, floodHeads, floodLamps, type FloodlightsField, type FloodMast }
  from '../../src/render/lights/floodlights';
import { coronaProfile, SPRITE } from '../../src/render/lights/sprites';
import { lightsSignature, tileBounds, type TileLightsField } from '../../src/render/lights/tileLights';

/** Shape of pipeline/turku3d/coarse/floodlights.py `tile_floodlights` for t0_0_0: one owned venue, a mapped mast,
 *  a laid-out mast and a mast of the neighbour tile (ignored). */
const FIELD: FloodlightsField = {
  version: 1,
  venues: [{ id: 'osm:way:1', kind: 'large', x: 100, y: 20, z: 100, ux: 1, uz: 0, length: 100, width: 60, level: 0.85 }],
  masts: [
    { id: 'osm:2', venue: 'osm:way:1', x: 50, y: 20, z: 60, top: 59, aim: [100, 100], lamps: 10, mesh: 1 },
    { id: 'osm:way:1:mast:0', venue: 'osm:way:1', x: 150, y: 20, z: 144, top: 40, aim: [100, 100], lamps: 5, mesh: 0 },
    { id: 'osm:9', venue: 'osm:way:1', x: -5, y: 20, z: 60, top: 40, aim: [100, 100], lamps: 5, mesh: 1 },
  ],
};
const LIGHTS: TileLightsField = { format: 'turku3d-lights', version: 1, count: 0, posts: 0, street: 0, data: '', floodlights: FIELD };

describe('venue floodlights', () => {
  test('a tile keeps only the venues and masts it owns', () => {
    const tile = decodeFloodlights(FIELD, tileBounds('t0_0_0'))!;
    expect(tile.venues.map(v => v.id)).toEqual(['osm:way:1']);
    expect(tile.masts.map(m => m.id)).toEqual(['osm:2', 'osm:way:1:mast:0']);
    expect(decodeFloodlights({ ...FIELD, version: 2 }, null)).toBeNull();
    expect(decodeFloodlights(undefined, null)).toBeNull();
  });

  test('lamp heads sit at the mast top in rows across the aim, half a metre toward the field', () => {
    const mast = FIELD.masts[0] as FloodMast;
    const heads = floodHeads(mast);
    expect(heads).toHaveLength(10);
    const aim = new THREE.Vector2(mast.aim[0] - mast.x, mast.aim[1] - mast.z).normalize();
    for (const h of heads) {
      const toward = (h.x - mast.x) * aim.x + (h.z - mast.z) * aim.y;
      expect(toward).toBeCloseTo(0.5, 5);
      expect(Math.abs(h.y - mast.top)).toBeLessThan(1.5);
    }
    // Two rows of five, centred across the aim direction.
    const across = heads.map(h => -(h.x - mast.x) * aim.y + (h.z - mast.z) * aim.x);
    expect(Math.max(...across)).toBeCloseTo(2 * FLOOD.spacing, 5);
    expect(Math.min(...across)).toBeCloseTo(-2 * FLOOD.spacing, 5);
    expect(new Set(heads.map(h => h.y.toFixed(3))).size).toBe(2);
  });

  test('heads are glare sprites of the one sprite draw and never pools or posts', () => {
    const lamps = floodLamps(decodeFloodlights(FIELD, tileBounds('t0_0_0')));
    expect(lamps).toHaveLength(15);
    expect(lamps.every(l => l.kind === 'flood' && !l.post && l.light.glare === 1 && l.light.day === 0)).toBe(true);
    expect(coronaProfile(undefined, 1)[0]).toBe(SPRITE.glarePx);
    expect(coronaProfile(undefined, 1)[3]).toBe(1);
  });

  test('the tile light list carries the floodlights through LampField', () => {
    const field = new LampField();
    let out: Lamp[] = [], venues = 0;
    field.onTile = (_id, lamps, _footprint, flood) => { out = lamps ?? []; venues = flood?.venues.length ?? 0; };
    field.sync([{ id: 't0_0_0', model: new THREE.Group(), lights: LIGHTS }]);
    field.step(Infinity);
    expect(out.filter(l => l.kind === 'flood')).toHaveLength(15);
    expect(venues).toBe(1);
    expect(lightsSignature(LIGHTS)).not.toBe(lightsSignature({ ...LIGHTS, floodlights: undefined }));
  });

  test('only masts the city does not draw get a pole, scaled to the mast and facing the field', () => {
    const masts = new FloodMasts(new THREE.MeshStandardNodeMaterial());
    expect(masts.mesh.count).toBe(1);
    expect(masts.setMasts(FIELD.masts)).toBe(1);
    const m = new THREE.Matrix4(); masts.mesh.getMatrixAt(0, m);
    const p = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
    m.decompose(p, q, s);
    expect([p.x, p.y, p.z]).toEqual([150, 20, 144]);
    expect(s.y).toBeCloseTo(20, 5);
    const facing = new THREE.Vector3(1, 0, 0).applyQuaternion(q);
    const aim = new THREE.Vector3(100 - 150, 0, 100 - 144).normalize();
    expect(facing.dot(aim)).toBeCloseTo(1, 5);
    expect(masts.setMasts([])).toBe(0);
    expect(masts.mesh.count).toBe(1);
  });
});
