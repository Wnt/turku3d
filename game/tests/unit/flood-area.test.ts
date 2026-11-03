import { describe, expect, test } from 'vitest';
import * as T from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { GameLighting } from '../../src/render/lighting';
import { wetSurface } from '../../src/render/weather/surfaces';
import { AREA, FloodArea, areaVenues, fieldDistance, floodAreaState, selectVenues, venueLight, type AreaVenue } from '../../src/render/lights/floodArea';
import type { FloodMast, FloodVenue } from '../../src/render/lights/floodlights';

/** A 104 x 64 m stadium pitch under 30 m masts, as osm:way:29386587 (t0_0_3) is served. */
const PITCH: FloodVenue = { id: 'v', kind: 'stadium', x: 0, y: 0, z: 0, ux: 1, uz: 0, length: 104, width: 64, level: 1 };
const mast = (venue: string, top: number): FloodMast => ({ id: `${venue}:${top}`, venue, x: 0, y: 0, z: 0, top, aim: [0, 0], lamps: 8, mesh: 0 });
const [V] = areaVenues([PITCH], [mast('v', 30), mast('v', 30)]);
const UP = { x: 0, y: 1, z: 0 };
const at = (x: number, z: number, y = 0, n = UP) => venueLight(V, 1, { x, y, z }, n);
const total = (l: { direct: number; ambient: number }) => l.direct + l.ambient;

describe('floodlit area light', () => {
  test('the pitch is one even, bright wash', () => {
    const centre = total(at(0, 0)), touchline = total(at(52, 32)), wing = total(at(0, 32));
    expect(centre).toBeCloseTo(AREA.field * (1 + AREA.ambient), 1);
    // Over five times a street pool under its lamp (about 0.6), well over half of open ground by day (about 5).
    expect(centre).toBeGreaterThan(3);
    expect(touchline / centre).toBeGreaterThan(0.8);
    expect(wing / centre).toBeGreaterThan(0.85);
  });

  test('light falls off softly onto stands and track', () => {
    const field = at(0, 0).direct;
    const stand = at(0, 32 + 12, 4, { x: 0, y: 0.7, z: -0.71 });
    expect(stand.direct / field).toBeGreaterThan(0.6);
    expect(at(0, 32 + 30).direct / field).toBeLessThan(0.5);
    // A player's vertical face on the pitch still gets the all-direction share.
    expect(at(0, 0, 1, { x: 1, y: 0, z: 0 }).ambient).toBeGreaterThan(0.7);
    // A facade facing away from the venue gets only the unshadowed share.
    expect(at(0, 32 + 20, 10, { x: 0, y: 0, z: 1 }).direct).toBe(0);
  });

  test('the venue radiates a few hundred metres: facades facing it pick up a fading share', () => {
    const facing = (r: number) => at(0, 32 + r, 10, { x: 0, y: 0, z: -1 }).direct;
    expect(facing(150)).toBeGreaterThan(0.08);
    expect(facing(200)).toBeGreaterThan(0.03);
    expect(facing(200)).toBeLessThan(0.2);
    expect(facing(300)).toBeGreaterThan(0.01);
    expect(facing(300)).toBeLessThan(facing(200));
    expect(total(at(0, 32 + AREA.reach + 1))).toBe(0);
    // Roofs above the lamp heads get none from above.
    expect(at(0, 60, 2 * V.height + 1).direct).toBe(0);
    expect(at(0, 60, 2 * V.height + 1).ambient).toBe(0);
  });

  test('a turned field follows its axis', () => {
    const [turned] = areaVenues([{ ...PITCH, ux: 0, uz: 2 }], []);
    expect(fieldDistance(turned, 0, 52)).toBe(0);
    expect(fieldDistance(turned, 52, 0)).toBeCloseTo(20, 5);
    expect(total(venueLight(turned, 1, { x: 0, y: 0, z: 50 }, UP))).toBeGreaterThan(0.8 * total(at(0, 0)));
  });

  test('lamp height comes from the resident masts, else the layout height of the kind', () => {
    const venues = areaVenues([PITCH, { ...PITCH, id: 'w', kind: 'court', y: 5 }, PITCH], [mast('v', 39), mast('v', 15)]);
    expect(venues.map(v => [v.id, v.height])).toEqual([['v', 27], ['w', AREA.height.court]]);
    expect([venues[0].a, venues[0].b]).toEqual([52, 32]);
  });

  test('the nearest venues take the slots and hand over without a pop', () => {
    const row: AreaVenue[] = [0, 1, 2, 3, 4, 5].map(i => ({ ...V, id: `v${i}`, x: i * 400 }));
    expect(selectVenues(row, 0, 0).map(s => s.venue.id)).toEqual(['v0', 'v1', 'v2', 'v3']);
    // One slot between two venues 1 km apart: the weight is 0 where the nearer one changes.
    const pair = [{ ...V, id: 'a' }, { ...V, id: 'b', x: 1000 }];
    const one = (x: number) => selectVenues(pair, x, 0, 1).map(s => [s.venue.id, s.weight]);
    expect(one(400)).toEqual([['a', 1]]);
    expect(one(450)).toEqual([['a', 0.5]]);
    expect(one(500)).toEqual([]);
    expect(one(550)).toEqual([['b', 0.5]]);
    // Nothing beyond the range.
    expect(selectVenues([{ ...V, x: AREA.range + 200 }], 0, 0)).toEqual([]);
    const area = new FloodArea();
    area.setVenues(row);
    area.update(0, 0, 0);
    expect(floodAreaState.count).toBe(0);
    area.update(0, 0, 1);
    expect(floodAreaState.count).toBe(4);
    expect(area.snapshot()[0]).toEqual({ id: 'v0', scale: 1 });
    area.dispose();
    expect(floodAreaState.count).toBe(0);
  });

  test('every lit material family carries the area light without a new uniform binding', () => {
    const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
    renderer.hasFeature = () => false;
    Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
    type Builder = NodeBuilder & { scene: T.Scene; camera: T.Camera; lightsNode: T.LightsNode; build(): void; fragmentShader: string };
    const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(object: T.Object3D, renderer: T.WebGPURenderer): Builder };
    const build = (material: T.Material, lightsNode: T.LightsNode) => {
      const builder = backend.createNodeBuilder(new T.Mesh(new T.BoxGeometry(), material), renderer);
      builder.scene = new T.Scene(); builder.camera = new T.PerspectiveCamera(); builder.lightsNode = lightsNode;
      builder.build();
      return builder.fragmentShader;
    };
    const lights = [new T.DirectionalLight(), new T.HemisphereLight(), new T.PointLight()];
    const lighting = new GameLighting({ point: 4 }), withArea = lighting.createNode(lights);
    const without = lighting.createNode(lights);
    (without as unknown as { _dataNodes: Map<string, unknown> })._dataNodes.delete('FloodAreaLight');
    const bindings = (wgsl: string) => (wgsl.match(/var<uniform>/g) ?? []).length;
    // Dry (grass pitches, rubber tracks, paint) and wet (asphalt courts) surfaces both receive it.
    for (const material of [new T.MeshStandardNodeMaterial(), wetSurface(new T.MeshStandardNodeMaterial()), new T.MeshLambertNodeMaterial()]) {
      const shader = build(material, withArea);
      expect(shader).toContain('floodCount');
      expect(shader).toContain('floodTint3');
      expect(bindings(shader)).toBe(bindings(build(material.clone(), without)));
    }
    expect(build(new T.MeshBasicNodeMaterial(), withArea)).not.toContain('floodCount');
  });
});
