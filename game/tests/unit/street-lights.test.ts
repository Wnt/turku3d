import { nightFactor } from '../../src/render/timecycle';
import { describe, expect, test } from 'vitest';
import { readFileSync } from 'node:fs';
import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { LampField, type Lamp, type LampSource } from '../../src/render/lights/lampField';
import { CORONA_RADIUS, decodeTileLights, lightsSignature, owns, tileBounds, type TileLightsField } from '../../src/render/lights/tileLights';
import { scanTileNow, mergeHeads } from '../../src/render/lights/tileScan';
import { LampPosts, POST_FADE, POST_SHAPE, postFade } from '../../src/render/lights/lampPosts';
import { LightSprites, SPRITE, spriteLevel, spritePixels } from '../../src/render/lights/sprites';
import { nightLevel } from '../../src/render/lights/night';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { parseParams } from '../../src/core/params';
import { PostProcessing } from '../../src/render/post';

/** pipeline/turku3d/props/lights.py `encode` of: a street post at (50, 3, 8) with its arm toward -z (yaw pi/2), a
 *  path post at (119.04, 4, 40) with yaw 0 and scale 0.6, a lamp hung at (30, 10, 0) and a facade light at
 *  (20, 6.25, -9.5). Street lights come first, posts before hung lights. */
const FIXTURE: TileLightsField = { format: 'turku3d-lights', version: 1, count: 4, posts: 2, street: 3,
  data: 'AABIQq5HKUHNzMxAn4lsgAAA8EKcxAhBAAAgQnNlVYEAAPBBAAAgQQAAAACfiWyCAACgQQAAyEAAABjBOSoaAwAASEIAAEBAAAAAQQBAZAB7FO5CAACAQAAAIEIAADwA' };

function lampsOf(source: LampSource): Lamp[] {
  const field = new LampField();
  let out: Lamp[] = [];
  field.onTile = (_id, lamps) => { out = lamps ?? []; };
  field.sync([source]); field.step(Infinity);
  return out;
}

describe('tile light list', () => {
  test('the generator records decode into posts with their heads, hung lights and facade lights', () => {
    const lamps = decodeTileLights(FIXTURE);
    expect(lamps.map(l => [l.post, l.light.radius])).toEqual([[true, CORONA_RADIUS.street], [true, CORONA_RADIUS.path],
      [false, CORONA_RADIUS.hung], [false, CORONA_RADIUS.wall]]);
    expect(lamps.map(l => l.kind)).toEqual(['street', 'street', 'street', 'wall']); // facade lights take no pool
    const [street, path, hung, wall] = lamps;
    expect([street.x, street.y, street.z, street.scale]).toEqual([50, 3, 8, 1]);
    expect(street.yaw).toBeCloseTo(Math.PI / 2, 3);
    // The corona sits at the post's diffuser: base + arm along local +X (three.js rotation.y) + light height.
    const head = new THREE.Vector3(POST_SHAPE.arm, POST_SHAPE.light, 0).multiplyScalar(street.scale)
      .applyAxisAngle(new THREE.Vector3(0, 1, 0), street.yaw).add(new THREE.Vector3(street.x, street.y, street.z));
    expect(street.light.x).toBeCloseTo(head.x, 4); expect(street.light.y).toBeCloseTo(head.y, 4); expect(street.light.z).toBeCloseTo(head.z, 4);
    expect(path.scale).toBe(0.6); expect(path.x).toBeCloseTo(119.04, 4);
    expect([hung.x, hung.y, hung.z]).toEqual([30, 10, 0]);
    expect([wall.x, wall.y, wall.z]).toEqual([20, 6.25, -9.5]);
    // Colours are HDR intensity x colour; facade lights are dimmer and warmer than street LEDs.
    expect(street.light.r).toBeCloseTo(5, 1); expect(wall.light.r).toBeCloseTo(1.8, 1);
    expect(wall.light.b / wall.light.r).toBeLessThan(street.light.b / street.light.r);
    for (const l of lamps) expect(l.light.day).toBe(0);
  });

  test('the client post matches the generator post its lights and collision assume', () => {
    const source = readFileSync(new URL('../../../pipeline/turku3d/props/lights.py', import.meta.url), 'utf8');
    const shape = /POST_SHAPE = \{"height": ([\d.]+), "arm": ([\d.]+), "light": ([\d.]+)\}/.exec(source);
    expect(shape?.slice(1).map(Number)).toEqual([POST_SHAPE.height, POST_SHAPE.arm, POST_SHAPE.light]);
  });

  test('missing, foreign or truncated light lists decode to no lamps', () => {
    expect(decodeTileLights(null)).toEqual([]);
    expect(decodeTileLights({ ...FIXTURE, version: 2 })).toEqual([]);
    expect(decodeTileLights({ ...FIXTURE, count: 5 })).toEqual([]);
    expect(decodeTileLights({ ...FIXTURE, posts: 5, count: 1 })).toEqual([]);
  });

  test('a tile combines its light list with the tunnel strips and bridge lamps built as geometry', () => {
    const model = new THREE.Group();
    const strips = new THREE.BufferGeometry();
    strips.setAttribute('position', new THREE.Float32BufferAttribute([9.6, 5, 50, 10.4, 5, 50, 10.4, 5, 50.2, 9.6, 5, 50.2], 3));
    strips.setIndex([0, 1, 2, 0, 2, 3]);
    const tunnel = new THREE.Mesh(strips, new THREE.MeshStandardMaterial());
    tunnel.userData = { layer: 'structures', objects: [{ object_id: 'tunnel:7:lights', start: 0, count: 6, layer: 'structures' }] };
    model.add(tunnel);
    const lamps = lampsOf({ id: 't0_0_0', model, lights: FIXTURE });
    expect(lamps).toHaveLength(5);
    expect(lamps.filter(l => l.post)).toHaveLength(2);
    const strip = lamps.at(-1)!;
    expect([Math.round(strip.x), Math.round(strip.z), strip.light.day]).toEqual([10, 50, 1]);
    // A strip owned by the neighbour tile is that tile's light.
    expect(lampsOf({ id: 't0_1_0', model, lights: null })).toEqual([]);
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
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(250, 250), new THREE.MeshStandardMaterial());
    ground.userData = { layer: 'ground', object_id: 'ground:t0_0_0' };
    model.add(tunnel, bridge, ground);
    const scan = scanTileNow(model);
    expect(scan.tunnel.map(l => [Math.round(l.x), Math.round(l.z)])).toEqual([[10, 50], [20, 50], [30, 50]]);
    expect(scan.bridge).toHaveLength(1);
    expect(scan.bridge[0].x).toBeCloseTo(100, 5); expect(scan.bridge[0].z).toBeCloseTo(100, 5);
    expect(mergeHeads([{ x: 0, y: 0, z: 0 }, { x: 3, y: 0, z: 0 }])).toHaveLength(2);
  });

  test('a re-polled but unchanged light list does not redo the tile; a changed one or a new model does', () => {
    const model = new THREE.Group(), field = new LampField(), calls: string[] = [];
    field.onTile = id => calls.push(id);
    field.sync([{ id: 't0_0_0', model, lights: FIXTURE }]); field.step(Infinity);
    field.sync([{ id: 't0_0_0', model, lights: structuredClone(FIXTURE) }]); field.step(Infinity);
    expect(calls).toEqual(['t0_0_0']); expect(field.pending()).toBe(false);
    field.sync([{ id: 't0_0_0', model, lights: { ...FIXTURE, count: 3, posts: 1, data: FIXTURE.data } }]); field.step(Infinity);
    expect(calls).toEqual(['t0_0_0', 't0_0_0']);
    field.sync([{ id: 't0_0_0', model: new THREE.Group(), lights: { ...FIXTURE, count: 3, posts: 1 } }]); field.step(Infinity);
    expect(calls).toHaveLength(3);
    field.sync([]);
    expect(calls).toHaveLength(4); expect(field.size).toBe(0);
    expect(lightsSignature(FIXTURE)).toBe(lightsSignature(structuredClone(FIXTURE)));
  });

  test('a light on a shared seam belongs to exactly one tile', () => {
    expect(owns(tileBounds('t0_0_0')!, 250, 10)).toBe(false);
    expect(owns(tileBounds('t0_1_0')!, 250, 10)).toBe(true);
    expect(tileBounds('t1_0_0')).toBeNull();
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
    expect(nightLevel({ time: new Date(), sunElevation: -20 })).toBe(1);
    expect(nightLevel({ time: new Date(), sunElevation: -4 })).toBe(nightFactor(-4));
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

  test('the sprite layer renders after the temporal resolve and bloom, tested against the opaque prepass depth', () => {
    const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera();
    for (const [tier, aa] of [['medium', 'taa'], ['mobile', 'fxaa']] as const) {
      const sprites = new LightSprites(tier);
      const post = new PostProcessing(renderer, scene, camera, tier, aa, sprites);
      const internals = post as unknown as { pipeline: { _update(): void; _quadMesh: THREE.Mesh }; pre: THREE.PassNode | null; beauty: THREE.PassNode };
      const depth = (sprites as unknown as { sceneDepth: { value: THREE.Texture } }).sceneDepth.value;
      expect(depth).toBe((internals.pre ?? internals.beauty).getTexture('depth'));
      if (tier === 'medium') {
        internals.pipeline._update();
        const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(object: THREE.Object3D, renderer: THREE.WebGPURenderer): ShaderBuilder & { updateBeforeNodes: THREE.Node[] } };
        const builder = backend.createNodeBuilder(internals.pipeline._quadMesh, renderer);
        builder.scene = scene; builder.camera = camera; builder.build();
        const order = builder.updateBeforeNodes.map(n => (n as THREE.PassNode).scene === sprites.scene ? 'sprites' : n.constructor.name);
        expect(order.at(-1)).toBe('sprites');
        expect(order.indexOf('TRAANode')).toBeLessThan(order.indexOf('sprites'));
        expect(order.filter(n => n === 'sprites')).toHaveLength(1);
      }
      post.dispose();
    }
  });
});
