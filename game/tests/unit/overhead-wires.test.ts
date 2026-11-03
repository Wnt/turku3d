import { describe, expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { LampField, type Lamp } from '../../src/render/lights/lampField';
import { lightsSignature, tileBounds, type TileLightsField } from '../../src/render/lights/tileLights';
import { OverheadWires, WIRE, decodeWires, wireAt, wireGeometry, wireLamps, withoutReplaced, type WiresField }
  from '../../src/render/lights/wires';

/** Shape of pipeline/turku3d/coarse/wires.py `record` for t0_0_0: a span wire across a street in two halves meeting
 *  at the hung lamp, a utility line, and a wire and a lamp of the neighbour tile (ignored). */
const FIELD: WiresField = {
  version: 1,
  kinds: ['span', 'utility', 'power'],
  wires: [
    [100, 14, 50, 112, 13.2, 51, 0.1, 0],
    [112, 13.2, 51, 124, 14.1, 52, 0.1, 0],
    [20, 9, 20, 60, 9.5, 20, 1.2, 1],
    [-30, 9, 20, -10, 9, 20, 0.5, 1],
  ],
  lamps: [[112, 13.2, 51, 0], [-20, 9, 20, 3]],
};
const LIGHTS: TileLightsField = { format: 'turku3d-lights', version: 1, count: 0, posts: 0, street: 0, data: '', wires: FIELD };

describe('overhead wires', () => {
  test('a tile keeps only the wires whose middle and the lamps it owns', () => {
    const tile = decodeWires(FIELD, tileBounds('t0_0_0'))!;
    expect(tile.wires.map(w => w.kind)).toEqual(['span', 'span', 'utility']);
    expect(tile.lamps).toEqual([{ x: 112, y: 13.2, z: 51 }]);
    expect(decodeWires({ ...FIELD, version: 2 }, null)).toBeNull();
    expect(decodeWires({ ...FIELD, wires: [[1, 2, 'x']], lamps: [] } as unknown as WiresField, null)).toBeNull();
    expect(decodeWires(undefined, null)).toBeNull();
  });

  test('a wire hangs its sag below the chord at mid span and meets both ends', () => {
    const w = decodeWires(FIELD, null)!.wires[2];
    expect(wireAt(w, 0)).toEqual({ x: 20, y: 9, z: 20 });
    expect(wireAt(w, 1)).toEqual({ x: 60, y: 9.5, z: 20 });
    expect(wireAt(w, 0.5).y).toBeCloseTo(9.25 - 1.2, 6);
  });

  test('ribbons: two vertices per station along the sag, one metre apart, with tangents along the wire', () => {
    const wires = decodeWires(FIELD, tileBounds('t0_0_0'))!.wires;
    const g = wireGeometry([wires[2]]);
    const n = Math.ceil(Math.hypot(40, 0) / WIRE.segment);
    expect(g.getAttribute('position').count).toBe((n + 1) * 2);
    expect(g.getIndex()!.count).toBe(n * 6);
    const p = g.getAttribute('position'), t = g.getAttribute('wireTangent'), side = g.getAttribute('wireSide');
    const middle = n;  // station n / 2, first vertex
    expect(p.getY(middle)).toBeCloseTo(9.25 - 1.2, 4);
    expect(Math.abs(t.getX(middle))).toBeGreaterThan(0.99);
    expect([side.getX(0), side.getX(1)]).toEqual([-1, 1]);
    expect(g.getAttribute('wireRadius').getX(0)).toBeCloseTo(WIRE.radius.utility, 6);
  });

  test('a hung lamp lights the street below its wire, and replaces the listed lamp hung there', () => {
    const tile = decodeWires(FIELD, tileBounds('t0_0_0'));
    const [lamp] = wireLamps(tile);
    expect(lamp.kind).toBe('street');
    expect(lamp.post).toBe(false);
    expect(lamp.light.y).toBeCloseTo(13.2 - WIRE.lamp.drop, 6);
    const listed: Lamp[] = [
      { ...lamp, key: 'a', x: 113, z: 52 },                        // the register's lamp hung over the street
      { ...lamp, key: 'b', x: 113, z: 52, post: true },            // a post beside it stays
      { ...lamp, key: 'c', x: 140, z: 52 },                        // another hung lamp stays
    ];
    expect(withoutReplaced(listed, [lamp]).map(l => l.key)).toEqual(['b', 'c']);
  });

  test('the light list carries the wires through LampField, and a changed list is new work', () => {
    const field = new LampField();
    let lamps: Lamp[] = [], wires = 0;
    field.onTile = (_id, list, _footprint, _flood, overhead) => { lamps = list ?? []; wires = overhead?.wires.length ?? 0; };
    field.sync([{ id: 't0_0_0', model: new THREE.Group(), lights: LIGHTS }]);
    field.step(1000);
    expect(wires).toBe(3);
    expect(lamps.filter(l => l.kind === 'street')).toHaveLength(1);
    expect(lightsSignature(LIGHTS)).not.toBe(lightsSignature({ ...LIGHTS, wires: { ...FIELD, lamps: [] } }));
  });

  test('one draw for every wire, one for the hung lamps; both exist before any tile has wires', () => {
    const overhead = new OverheadWires(new THREE.MeshStandardNodeMaterial());
    expect(overhead.mesh.geometry.getAttribute('position').count).toBeGreaterThan(0);
    expect(overhead.lamps.count).toBe(1);
    const tile = decodeWires(FIELD, tileBounds('t0_0_0'))!;
    expect(overhead.setTiles([tile, tile])).toEqual({ wires: 6, lamps: 2 });
    expect(overhead.wires).toBe(6);
    const camera = new THREE.PerspectiveCamera(60, 16 / 9, 0.1, 1000);
    overhead.update(camera, new THREE.Vector2(1920, 1080));
    expect(overhead.pixelAngle.value).toBeCloseTo(2 * Math.tan(Math.PI / 6) / 1080, 9);
    overhead.dispose();
  });
});
