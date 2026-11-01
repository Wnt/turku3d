import { describe, expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { CAR, FOOT, ONEWAY, mergeRoadGraphs, type RoadGraphJson } from '../../src/gameplay/roadGraph';
import { LAMP_RULE, lampStations, tileBounds, owns } from '../../src/render/lights/lampRule';
import { LampField, lampProbes, placeLamps, type Lamp, type LampSource } from '../../src/render/lights/lampField';
import { scanTileNow, mergeHeads } from '../../src/render/lights/tileScan';
import { LampPosts, POST_FADE, postFade } from '../../src/render/lights/lampPosts';
import { LightSprites, SPRITE, spriteLevel, spritePixels } from '../../src/render/lights/sprites';
import { nightLevel } from '../../src/render/lights/night';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { parseParams } from '../../src/core/params';

const S = LAMP_RULE.spacing;
const street = (cls: number, oneway = false) => CAR | FOOT | (oneway ? ONEWAY : 0) | cls << 3;

/** One tile's roads.json: polylines (engine x,z pairs) clipped by the caller to the tile, as the pipeline does. */
function tileRoads(id: string, ways: { points: number[][]; flags: number }[]): RoadGraphJson {
  const nodes: number[] = [], edges: number[][] = [];
  const node = (x: number, z: number) => {
    for (let i = 0; i < nodes.length; i += 2) if (Math.hypot(nodes[i] - x, nodes[i + 1] - z) < 0.05) return i / 2;
    nodes.push(x, z); return nodes.length / 2 - 1;
  };
  for (const way of ways) {
    const a = node(way.points[0][0], way.points[0][1]), last = way.points[way.points.length - 1], b = node(last[0], last[1]);
    let length = 0;
    for (let i = 1; i < way.points.length; i++) length += Math.hypot(way.points[i][0] - way.points[i - 1][0], way.points[i][1] - way.points[i - 1][1]);
    edges.push([a, b, way.flags, length, ...way.points.slice(1, -1).flat()]);
  }
  return { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox: [...tileBounds(id)!], nodes, edges };
}

/** A street from x0 to x1 at z, split at every tile seam (and at `splits`), as roads.json clips it. */
function eastWest(z: number, x0: number, x1: number, flags: number, splits: number[] = []) {
  const cuts = [...new Set([x0, x1, ...splits, ...[-250, 0, 250, 500, 750].filter(x => x > x0 && x < x1)])].sort((a, b) => a - b);
  const byTile = new Map<string, { points: number[][]; flags: number }[]>();
  for (let i = 1; i < cuts.length; i++) {
    const id = `t0_${Math.floor((cuts[i - 1] + cuts[i]) / 2 / 250)}_${Math.floor(z / 250)}`;
    const list = byTile.get(id) ?? [];
    list.push({ points: [[cuts[i - 1], z], [cuts[i], z]], flags });
    byTile.set(id, list);
  }
  return byTile;
}

/** Flat ground covering a tile, optionally with building footprints (roof quads) and water. */
function tileModel(id: string, extras: { buildings?: number[][]; water?: number[][]; y?: number } = {}) {
  const [x0, z0, x1, z1] = tileBounds(id)!;
  const y = extras.y ?? 2;
  const model = new THREE.Group();
  const quad = (b: number[], height: number, layer: string, key: string) => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute([b[0], height, b[1], b[2], height, b[1], b[2], height, b[3], b[0], height, b[3]], 3));
    g.setIndex([0, 2, 1, 0, 3, 2]);
    const mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial());
    mesh.userData = { layer, object_id: `${layer}:${key}`, material_key: key };
    model.add(mesh);
  };
  quad([x0, z0, x1, z1], y, 'ground', 'draft_asphalt_worn');
  for (const [i, b] of (extras.buildings ?? []).entries()) quad(b, y + 12, 'buildings', `facade_kit_${i}`);
  for (const b of extras.water ?? []) quad(b, y + 0.5, 'ground', 'water');
  return model;
}

function lampsOf(source: LampSource): Lamp[] {
  const field = new LampField();
  let out: Lamp[] = [];
  field.onTile = (_id, lamps) => { out = lamps ?? []; };
  field.sync([source]); field.step(Infinity);
  return out;
}

describe('lamp rule', () => {
  test('a street keeps its spacing across tile seams and plain nodes', () => {
    // 0..700 m east-west, split at two seams, a plain way break at 120 m and a footway crossing node at 400 m.
    const tiles = eastWest(100, 0, 700, street(2), [120, 400]);
    const stations = [...tiles].flatMap(([id, ways]) => lampStations(tileRoads(id, ways), tileBounds(id)!));
    const xs = stations.map(s => s.x).sort((a, b) => a - b);
    expect(xs.length).toBe(Math.round(250 / S) * 2 + Math.round(200 / S));
    // The plain way break at 120 m does not end the chain: the first lamp is half a spacing from the street's end.
    expect(xs[0]).toBeCloseTo(250 / Math.round(250 / S) / 2, 6);
    const gaps = xs.slice(1).map((x, i) => x - xs[i]);
    for (const gap of gaps) { expect(gap).toBeGreaterThan(0.8 * S); expect(gap).toBeLessThan(1.25 * S); }
    // Across the seams at 250 and 500 the gap is half a spacing on each side.
    const seamGaps = [250, 500].map(seam => xs.find(x => x > seam)! - xs.filter(x => x < seam).at(-1)!);
    for (const gap of seamGaps) expect(gap).toBeGreaterThan(0.8 * S);
  });

  test('placement depends only on world geometry: edge order, edge direction and load order do not matter', () => {
    const ways = [{ points: [[10, 30], [60, 32], [140, 35], [249.99, 40]], flags: street(2) },
      { points: [[60, 32], [70, 120], [65, 240]], flags: street(1) },
      { points: [[140, 35], [140, 200]], flags: street(3) },
      { points: [[100, 0], [100, 30]], flags: FOOT }];
    const json = tileRoads('t0_0_0', ways);
    const reversed = tileRoads('t0_0_0', [...ways].reverse().map(w => ({ ...w, points: [...w.points].reverse() })));
    const key = (list: { x: number; z: number }[]) => list.map(s => `${s.x.toFixed(3)},${s.z.toFixed(3)}`).sort();
    expect(key(lampStations(reversed))).toEqual(key(lampStations(json)));
    // mergeRoadGraphs renumbers nodes and edges in load order; the rule reads only each tile's own file.
    const renumbered = { ...json, nodes: [...json.nodes.slice(2), ...json.nodes.slice(0, 2)],
      edges: json.edges.map(([a, b, ...rest]) => [(a + json.nodes.length / 2 - 1) % (json.nodes.length / 2), (b + json.nodes.length / 2 - 1) % (json.nodes.length / 2), ...rest]) };
    expect(mergeRoadGraphs([renumbered]).edges.length).toBe(json.edges.length);
    expect(key(lampStations(renumbered))).toEqual(key(lampStations(json)));
    const tiles = [...eastWest(60, -200, 450, street(2))].map(([id, w]) => ({ id, model: tileModel(id), roads: tileRoads(id, w) }));
    const run = (order: typeof tiles) => {
      const field = new LampField(), out = new Map<string, Lamp[]>();
      field.onTile = (id, lamps) => { if (lamps) out.set(id, lamps); };
      for (const t of order) { field.sync(order.slice(0, order.indexOf(t) + 1)); field.step(Infinity); }
      return [...out.values()].flat().map(l => `${l.key}/${l.yaw.toFixed(4)}/${l.y}`).sort();
    };
    const forward = run(tiles);
    expect(forward.length).toBeGreaterThan(15);
    expect(run([...tiles].reverse())).toEqual(forward);
    expect(run([tiles[1], tiles[2], tiles[0]])).toEqual(forward);
  });

  test('wide two-way streets alternate sides, also across a seam; one-way streets keep the right-hand kerb', () => {
    const tiles = eastWest(100, 0, 500, street(3));
    const lamps = [...tiles].flatMap(([id, w]) => lampsOf({ id, model: tileModel(id), roads: tileRoads(id, w) }))
      .filter(l => l.post).sort((a, b) => a.x - b.x);
    expect(lamps.length).toBe(2 * Math.round(250 / S));
    lamps.forEach((l, i) => { if (i) expect(Math.sign(l.z - 100)).toBe(-Math.sign(lamps[i - 1].z - 100)); });
    for (const l of lamps) expect(Math.abs(Math.abs(l.z - 100) - LAMP_RULE.offset[3])).toBeLessThan(1e-6);
    // Heading east (+x), the right-hand side is south (+z); heading west it is north.
    for (const [x0, x1, sign] of [[20, 230, 1], [230, 20, -1]]) {
      const json = tileRoads('t0_0_0', [{ points: [[x0, 100], [x1, 100]], flags: street(2, true) }]);
      const one = lampsOf({ id: 't0_0_0', model: tileModel('t0_0_0'), roads: json }).filter(l => l.post);
      expect(one.length).toBe(Math.round(210 / S));
      for (const l of one) expect(Math.sign(l.z - 100)).toBe(sign);
    }
  });

  test('kerb points avoid buildings and water, and stand on the surface', () => {
    const json = tileRoads('t0_0_0', [{ points: [[20, 100], [230, 100]], flags: street(2) }]);
    const plain = lampsOf({ id: 't0_0_0', model: tileModel('t0_0_0', { y: 3 }), roads: json }).filter(l => l.post);
    const side = Math.sign(plain[0].z - 100);
    for (const l of plain) { expect(l.y).toBeCloseTo(3, 5); expect(Math.sign(l.z - 100)).toBe(side); }
    // A building block along the preferred kerb pushes every lamp to the other side; water there drops them.
    const block = side > 0 ? [0, 101.5, 250, 140] : [0, 60, 250, 98.5];
    const other = side > 0 ? [0, 60, 250, 98.5] : [0, 101.5, 250, 140];
    const moved = lampsOf({ id: 't0_0_0', model: tileModel('t0_0_0', { buildings: [block] }), roads: json }).filter(l => l.post);
    expect(moved.length).toBe(plain.length);
    for (const l of moved) expect(Math.sign(l.z - 100)).toBe(-side);
    const none = lampsOf({ id: 't0_0_0', model: tileModel('t0_0_0', { buildings: [block], water: [other] }), roads: json });
    expect(none.filter(l => l.post)).toEqual([]);
  });

  test('bridge decks use their own surface, and decks with generator lamps get no rule lamps', () => {
    const roads = tileRoads('t0_0_0', [{ points: [[20, 100], [230, 100]], flags: street(2) }]);
    roads.edgeHeights = [[9, 9]];
    const model = tileModel('t0_0_0');
    const deck = new THREE.Mesh(new THREE.PlaneGeometry(250, 20).rotateX(-Math.PI / 2).translate(125, 9.2, 100), new THREE.MeshStandardMaterial());
    deck.userData = { layer: 'bridges', object_id: 'draft-bridge:1:deck', material_key: 'draft_bridge_concrete' };
    model.add(deck);
    const lamps = lampsOf({ id: 't0_0_0', model, roads }).filter(l => l.post);
    expect(lamps.length).toBe(Math.round(210 / S));
    for (const l of lamps) expect(l.y).toBeCloseTo(9.2, 4);
    // A generator-built lamp diffuser (thin closed box) on the deck: the deck is already lit.
    const diffuser = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.016, 0.2).translate(125, 16, 106), new THREE.MeshStandardMaterial());
    diffuser.userData = { layer: 'bridges', object_id: 'draft-bridge:1:furniture:paint:3', material_key: 'draft_bridge_paint' };
    model.add(diffuser);
    const lit = lampsOf({ id: 't0_0_0', model, roads });
    expect(lit.filter(l => !l.post).map(l => [l.x, l.z].map(Math.round))).toEqual([[125, 106]]);
    expect(lit.filter(l => l.post).every(l => Math.abs(l.x - 125) >= 30)).toBe(true);
  });

  test('tunnel strips and bridge lamp heads become one light each; rails and deck paint do not', () => {
    const model = new THREE.Group();
    const strips = new THREE.BufferGeometry();
    const p: number[] = [];
    for (const x of [10, 20, 30]) p.push(x - 0.4, 5, 50, x + 0.4, 5, 50, x + 0.4, 5, 50.2, x - 0.4, 5, 50.2);
    strips.setAttribute('position', new THREE.Float32BufferAttribute(p, 3));
    strips.setIndex([0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7, 8, 9, 10, 8, 10, 11]);
    const tunnel = new THREE.Mesh(strips, new THREE.MeshStandardMaterial());
    tunnel.userData = { layer: 'structures', objects: [{ object_id: 'tunnel:7:lights', start: 0, count: 18, layer: 'structures' }] };
    const heads = [[0.55, 0], [-0.55, 0], [0, 0.55], [0, -0.55]].map(([dx, dz]) => new THREE.BoxGeometry(0.3, 0.016, 0.2).translate(100 + dx, 12, 100 + dz));
    const rail = new THREE.BoxGeometry(20, 0.05, 0.05).translate(110, 10, 90);
    const paint = new THREE.PlaneGeometry(3, 0.15).rotateX(-Math.PI / 2).translate(105, 9.01, 95);
    const group = [...heads, rail, paint].map(g => g.index ? g.toNonIndexed() : g);
    const merged = new THREE.BufferGeometry();
    merged.setAttribute('position', new THREE.Float32BufferAttribute(group.flatMap(g => [...g.attributes.position.array]), 3));
    const bridge = new THREE.Mesh(merged, new THREE.MeshStandardMaterial());
    bridge.userData = { layer: 'bridges', object_id: 'draft-bridge:9:furniture:paint:1' };
    model.add(tunnel, bridge);
    const scan = scanTileNow(model, []);
    expect(scan.tunnel.map(l => [Math.round(l.x), Math.round(l.z)])).toEqual([[10, 50], [20, 50], [30, 50]]);
    expect(scan.bridge).toHaveLength(1);
    expect(scan.bridge[0].x).toBeCloseTo(100, 5); expect(scan.bridge[0].z).toBeCloseTo(100, 5);
    expect(mergeHeads([{ x: 0, y: 0, z: 0 }, { x: 3, y: 0, z: 0 }])).toHaveLength(2);
  });

  test('a lamp on a shared seam belongs to exactly one tile', () => {
    expect(owns(tileBounds('t0_0_0')!, 250, 10)).toBe(false);
    expect(owns(tileBounds('t0_1_0')!, 250, 10)).toBe(true);
    const { points, candidates } = lampProbes(lampStations(tileRoads('t0_0_0', [{ points: [[0, 0], [249.9, 0.1]], flags: street(1) }])));
    expect(points.length).toBe(candidates.flat().length);
    expect(placeLamps([], [], { probes: [], tunnel: [{ x: 250, y: 0, z: 5 }], bridge: [] }, tileBounds('t0_0_0')!)).toEqual([]);
  });
});

describe('sprites and fades', () => {
  test('corona size is clamped to 2..48 output pixels', () => {
    const focal = 1.7320508 * 725 / 2;
    expect(spritePixels(0.85, 30, focal)).toBeCloseTo(2 * 0.85 * focal / 30, 6);
    expect(spritePixels(0.85, 2, focal)).toBe(SPRITE.maxPx);
    expect(spritePixels(0.85, 5000, focal)).toBe(SPRITE.minPx);
    expect(spritePixels(0.05, 300, focal)).toBe(SPRITE.minPx);
    expect(SPRITE.minPx).toBe(2); expect(SPRITE.maxPx).toBe(48);
    // A street lamp stays above the minimum to the edge of the 3x3 window at the Air's 725 px floor.
    expect(2 * 0.85 * focal / 350).toBeGreaterThan(SPRITE.minPx);
  });

  test('posts scale to zero between 105 and 125 m; sprites fade in near the camera and after streaming', () => {
    expect(postFade(0)).toBe(1); expect(postFade(POST_FADE.full)).toBe(1);
    expect(postFade((POST_FADE.full + POST_FADE.gone) / 2)).toBeCloseTo(0.5, 6);
    expect(postFade(POST_FADE.gone)).toBe(0); expect(postFade(400)).toBe(0);
    for (let d = 0; d < 140; d += 0.5) expect(postFade(d + 0.5)).toBeLessThanOrEqual(postFade(d));
    expect(spriteLevel(1, 0, 1, 10)).toBe(0);
    expect(spriteLevel(1, 0, 50, 10)).toBe(1);
    expect(spriteLevel(0, 0, 50, 10)).toBe(0);
    expect(spriteLevel(0, 1, 50, 10)).toBe(1);
    expect(spriteLevel(1, 0, 50, SPRITE.fadeIn / 2)).toBeCloseTo(0.5, 6);
    expect(nightLevel({ time: new Date(), sunElevation: 30 })).toBe(0);
    expect(nightLevel({ time: new Date(), sunElevation: -10 })).toBe(1);
  });

  test('posts keep stable slots, scale with distance and never draw zero instances', () => {
    const posts = new LampPosts(8);
    expect(posts.mesh.count).toBe(1);
    const lamps = [0, 50, 115, 300].map((x, i) => ({ key: String(i), x, y: 0, z: 0, yaw: 0, scale: 1 }));
    posts.setLamps(lamps); posts.update(0, 0, 0);
    expect(posts.drawn).toBe(3);
    const scale = (slot: number) => new THREE.Matrix4().fromArray(posts.mesh.instanceMatrix.array, slot * 16).getMaxScaleOnAxis();
    expect([scale(0), scale(1)]).toEqual([1, 1]); expect(scale(2)).toBeCloseTo(postFade(115), 6);
    posts.update(40, 0, 1000);
    expect(scale(0)).toBe(1); expect(scale(2)).toBe(1);
    posts.setLamps([]); posts.update(40, 0, 2000);
    expect(posts.drawn).toBe(0); expect(posts.mesh.count).toBe(1); expect(scale(0)).toBe(0);
  });

  test('sprite groups keep fade state for unchanged lights and keep the nearest when over capacity', () => {
    let now = 10;
    const sprites = new LightSprites('mobile', () => now);
    const camera = new THREE.PerspectiveCamera(), size = new THREE.Vector2(800, 600);
    const light = (x: number) => ({ x, y: 5, z: 0, radius: 0.85, r: 1, g: 1, b: 1, day: 0 });
    sprites.setGroup('a', [light(0), light(10)]); sprites.update(camera, size, 1);
    const born = sprites.mesh.geometry.getAttribute('lightBorn') as THREE.InstancedBufferAttribute;
    expect(sprites.count).toBe(2); expect(born.getX(0)).toBe(0);
    now = 20; sprites.update(camera, size, 1);
    sprites.setGroup('a', [light(0), light(20)]); sprites.update(camera, size, 1);
    expect([born.getX(0), born.getX(1)]).toEqual([0, 20]);
    sprites.setGroup('a', null); sprites.update(camera, size, 1);
    expect(sprites.count).toBe(0); expect(sprites.mesh.geometry.instanceCount).toBe(1);
    sprites.setGroup('b', Array.from({ length: sprites.capacity + 5 }, (_, i) => light(i))); sprites.update(camera, size, 1);
    expect(sprites.count).toBe(sprites.capacity); expect(sprites.dropped).toBe(5);
  });

  test('?lights= fixes the night level', () => {
    expect(parseParams('').lights).toBeNull();
    expect(parseParams('?lights').lights).toBe(1);
    expect(parseParams('?lights=0.25').lights).toBe(0.25);
    expect(() => parseParams('?lights=2')).toThrow('lights');
  });
});

describe('shader programs', () => {
  type ShaderBuilder = NodeBuilder & { scene: THREE.Scene; camera: THREE.Camera; build(): void; vertexShader: string; fragmentShader: string };
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  // No GPU device in Node: the depth texture's filterability query needs an initialized backend.
  Object.assign(renderer, { hasFeature: () => false });
  Object.assign(renderer.backend, { renderer });
  const build = (object: THREE.Mesh) => {
    const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(object: THREE.Object3D, renderer: THREE.WebGPURenderer): ShaderBuilder };
    const builder = backend.createNodeBuilder(object, renderer);
    builder.scene = new THREE.Scene(); builder.camera = new THREE.PerspectiveCamera();
    renderer.debug.onNodeBuilderCreated!(builder, { object });
    builder.build();
    return [builder.vertexShader, builder.fragmentShader];
  };

  test('the sprite draw compiles one program for any sprite count and night level', () => {
    const programs = [[1, 0], [4000, 1]].map(([count, night]) => {
      const sprites = new LightSprites('medium');
      sprites.setGroup('lamps', Array.from({ length: count }, (_, i) => ({ x: i, y: 5, z: 0, radius: 0.85, r: 3, g: 2, b: 1, day: 0 })));
      sprites.update(new THREE.PerspectiveCamera(), new THREE.Vector2(1280, 720), night);
      expect(sprites.mesh.geometry.instanceCount).toBe(count);
      return build(sprites.mesh);
    });
    expect(programs[1]).toEqual(programs[0]);
    expect(programs[0][1]).toMatch(/texture_depth_2d/);
  });

  test('the lamp-post draw compiles one program for any post count and night level', () => {
    const programs = [[1, 0], [600, 1]].map(([count, night]) => {
      const posts = new LampPosts(1024);
      posts.setLamps(Array.from({ length: count }, (_, i) => ({ key: String(i), x: i * 0.1, y: 0, z: 0, yaw: i, scale: 1 })));
      posts.update(0, 0, 0); posts.night.value = night;
      expect(posts.mesh.count).toBe(count);
      return build(posts.mesh);
    });
    expect(programs[1]).toEqual(programs[0]);
    expect(programs[0][0]).not.toMatch(/array<mat4x4<f32>,\s*\d+>/);
  });
});
