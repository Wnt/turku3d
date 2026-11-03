import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
// @ts-expect-error r186 renderer internals have no DefinitelyTyped declarations.
import RenderLists from 'three/src/renderers/common/RenderLists.js';
// @ts-expect-error r186 renderer internals have no DefinitelyTyped declarations.
import RenderBundles from 'three/src/renderers/common/RenderBundles.js';
import ClippingContext from 'three/src/renderers/common/ClippingContext.js';
import { StaticBundles, TileBundle, bundleable } from '../../src/render/staticBundles';
import { executeBundles, renderBundle } from '../../src/render/bundleReplay';
import { lodFades } from '../../src/render/lodFade';
import { WaterCoverage } from '../../src/render/reflections';

/* eslint-disable @typescript-eslint/no-explicit-any */
const surface = (layer = 'ground', x = 0) => {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(10, 1, 10), new THREE.MeshStandardNodeMaterial());
  mesh.position.set(x, 0, 0); mesh.userData.layer = layer; return mesh;
};

function tile() {
  const model = new THREE.Group(); model.position.set(1000, 0, 0);
  const ground = surface(), road = surface('ground', 20), wall = surface('structures', -20);
  const shadow = new THREE.Mesh(new THREE.BoxGeometry(5, 5, 5), new THREE.MeshBasicMaterial());
  shadow.userData.shadowBatch = true;
  const chunk = new THREE.Mesh(new THREE.BoxGeometry(5, 5, 5), shadow.material); chunk.userData.shadowBatch = true;
  chunk.position.set(30, 0, 0); shadow.add(chunk);
  const prop = surface('structures'); prop.userData.prop_type = 'sign';
  const placements = surface('buildings'); placements.userData.placementIds = ['p1'];
  const glass = surface('buildings'); (glass.material as THREE.Material).transparent = true;
  const trees = new THREE.InstancedMesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial(), 4); trees.userData.layer = 'vegetation';
  const shell = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial()); shell.name = 'block-lod-tile';
  model.add(ground, road, wall, shadow, prop, glass, trees, shell, placements);
  return { model, ground, road, wall, shadow, chunk, prop, glass, trees, shell, placements };
}

test('only unchanging tile surfaces and shadow batches are bundled', () => {
  const t = tile();
  expect([t.ground, t.road, t.wall, t.shadow].every(bundleable)).toBe(true);
  expect([t.prop, t.glass, t.trees, t.shell, t.placements].some(bundleable)).toBe(false);
  const cutout = surface(); (cutout.material as THREE.Material).alphaTest = 0.5; expect(bundleable(cutout)).toBe(false);
  const parent = surface(); parent.add(surface()); expect(bundleable(parent)).toBe(false);
  const callback = surface(); callback.onBeforeRender = () => {}; expect(bundleable(callback)).toBe(false);
  const hidden = surface(); hidden.visible = false; expect(bundleable(hidden)).toBe(false);
  const rows = surface(); rows.userData.objects = [{ prop_type: 'bus_stop' }]; expect(bundleable(rows)).toBe(false);
});

test('a landed tile moves its static draws into one bundle and restores them unchanged', () => {
  const scene = new THREE.Scene(), bundles = new StaticBundles(scene), t = tile();
  scene.add(t.model); scene.updateMatrixWorld(true);
  const before = new Map([t.ground, t.road, t.wall, t.shadow, t.chunk].map(o => [o, o.matrixWorld.clone()]));
  // Not before its install dissolve lands.
  const start = vi.spyOn(lodFades, 'has').mockReturnValue(true);
  bundles.add(t.model); bundles.update();
  expect(bundles.bundles.size).toBe(0);
  start.mockReturnValue(false); bundles.update(); start.mockRestore();
  const bundle = bundles.bundles.get(t.model)!;
  expect(bundle).toBeInstanceOf(TileBundle);
  expect((bundle as unknown as { isBundleGroup: boolean }).isBundleGroup).toBe(true);
  expect(bundle.parent).toBe(t.model);
  expect(bundle.children).toEqual([t.ground, t.road, t.wall, t.shadow]);
  for (const o of [t.prop, t.glass, t.trees, t.shell, t.placements]) expect(o.parent).toBe(t.model);
  expect([t.ground, t.shadow, t.chunk].every(o => !o.frustumCulled && !o.matrixAutoUpdate)).toBe(true);
  scene.updateMatrixWorld();
  for (const [o, m] of before) expect(o.matrixWorld.equals(m)).toBe(true);
  // A moved tile carries its bundle and bounds along.
  t.model.position.x += 500; scene.updateMatrixWorld(); bundles.update();
  expect(t.ground.matrixWorld.elements[12]).toBeCloseTo(1500);
  expect(bundle.bounds.min.x).toBeCloseTo(1475);
  expect(bundle.bounds.max.x).toBeCloseTo(1532.5);
  t.model.position.x -= 500; scene.updateMatrixWorld();
  bundles.remove(t.model);
  expect(bundle.parent).toBeNull();
  expect(t.model.children).toEqual(expect.arrayContaining([t.ground, t.road, t.wall, t.shadow]));
  expect([t.ground, t.shadow, t.chunk].every(o => o.frustumCulled && o.matrixAutoUpdate)).toBe(true);
  scene.updateMatrixWorld();
  for (const [o, m] of before) expect(o.matrixWorld.equals(m)).toBe(true);
  bundles.dispose();
});

test('any member change records again; a removed member leaves the bundle', () => {
  const scene = new THREE.Scene(), bundles = new StaticBundles(scene), t = tile();
  scene.add(t.model); bundles.add(t.model); bundles.update();
  const bundle = bundles.bundles.get(t.model)!, version = () => bundle.version;
  const v0 = version();
  bundles.update(); expect(version()).toBe(v0);
  const twin = new THREE.MeshStandardNodeMaterial();
  const original = t.ground.material; t.ground.material = twin; bundles.update(); expect(version()).toBe(v0 + 1);
  t.ground.material = original; bundles.update(); expect(version()).toBe(v0 + 2);
  t.wall.castShadow = !t.wall.castShadow; bundles.update(); expect(version()).toBe(v0 + 3);
  t.road.visible = false; bundles.update(); expect(version()).toBe(v0 + 4);
  (t.road.material as THREE.Material).needsUpdate = true; bundles.update(); expect(version()).toBe(v0 + 5);
  t.shadow.removeFromParent(); bundles.update(); expect(version()).toBe(v0 + 6);
  expect(bundle.members.map(m => m.object)).toEqual([t.ground, t.road, t.wall]);
  bundles.update(); expect(version()).toBe(v0 + 6);
  // A disposed material or geometry still drawn records again; a released tile leaves no listener behind.
  (t.wall.material as THREE.Material).dispose(); expect(version()).toBe(v0 + 7);
  t.road.geometry.dispose(); expect(version()).toBe(v0 + 8);
  const shared = t.ground.material as THREE.Material;
  bundles.remove(t.model);
  expect(shared.hasEventListener('dispose', (bundle as unknown as { invalidate: () => void }).invalidate)).toBe(false);
  shared.dispose(); expect(version()).toBe(v0 + 8);
  bundles.dispose();
});

test('each pass culls bundles by its own camera, and river reflections by their water', () => {
  const scene = new THREE.Scene(), bundles = new StaticBundles(scene), t = tile();
  scene.add(t.model); bundles.add(t.model); bundles.update();
  const bundle = bundles.bundles.get(t.model)!;
  const toward = new THREE.PerspectiveCamera(60, 1, 0.1, 5000), away = new THREE.PerspectiveCamera(60, 1, 0.1, 5000);
  toward.position.set(1000, 10, 200); toward.lookAt(1000, 0, 0); toward.updateMatrixWorld();
  away.position.set(1000, 10, 200); away.lookAt(1000, 0, 400); away.updateMatrixWorld();
  const render = (camera: THREE.Camera) => scene.onBeforeRender({} as never, scene, camera, null as never, null as never, null as never);
  render(toward); expect(bundle.visible).toBe(true);
  render(away); expect(bundle.visible).toBe(false);
  render(toward); expect(bundle.visible).toBe(true);
  // A reflection camera facing the tile whose water lies far away draws nothing of it.
  const coverage = new WaterCoverage(), water = new THREE.Mesh(new THREE.PlaneGeometry(20, 20).rotateX(-Math.PI / 2));
  water.position.set(-4000, 0, -4000); coverage.add(water);
  const plane = new THREE.Object3D(); plane.updateMatrixWorld();
  toward.userData.waterReflection = { coverage, eye: toward, plane };
  render(toward); expect(bundle.visible).toBe(false);
  water.position.set(1000, 0, 100); coverage.remove(water); coverage.add(water);
  render(toward); expect(bundle.visible).toBe(true);
  bundles.dispose();
});

/** A WebGPURenderer with just enough r186 state to project scenes and record/replay bundles without a GPU. */
function bundleRenderer() {
  vi.stubGlobal('requestAnimationFrame', () => 1);
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement }) as any;
  const data = new WeakMap<object, any>(), get = (o: object) => { let d = data.get(o); if (!d) data.set(o, d = {}); return d; };
  const executed: unknown[][] = [];
  const pass = { executeBundles: vi.fn((list: unknown[]) => executed.push([...list])) };
  const backend = {
    get, beginBundle: vi.fn(), addBundle: (context: object, bundle: object) => get(context).renderBundles.push(get(bundle).bundleGPU),
    finishBundle: vi.fn((_context: object, bundle: object) => { get(bundle).bundleGPU = { recorded: true }; }),
  };
  const context = { clippingContext: new ClippingContext() };
  Object.assign(get(context), { renderBundles: [], currentPass: pass, currentSets: { pipeline: 'stale' } });
  const ready = new Set<object>(), bindGroups = new Map<object, object>();
  Object.assign(renderer, {
    backend, _renderLists: new RenderLists(), _bundles: new RenderBundles(), _currentRenderContext: context, _currentRenderBundle: null,
    lighting: { enabled: true, getNode: () => ({ setLights: () => {} }) }, sortObjects: false,
    _nodes: { needsRefresh: vi.fn(() => 2), updateBefore: vi.fn(), updateForRender: vi.fn(), updateAfter: vi.fn() },
    _geometries: { updateForRender: vi.fn() }, _bindings: { updateForRender: vi.fn(), updateSharedForRender: vi.fn() },
    _pipelines: { isReady: (o: object) => ready.has(o) },
  });
  // Recording "draws" each item: one render object per mesh, with its own bind group, ready unless told otherwise.
  const objects = new Map<object, any>();
  renderer._renderObjects = vi.fn((list: { object: THREE.Mesh }[]) => {
    const bundle = get(renderer._currentRenderBundle);
    for (const { object } of list) {
      let ro = objects.get(object);
      if (!ro) {
        const group = {}; bindGroups.set(group, { id: 1 }); get(group).group = bindGroups.get(group);
        ro = { object, geometry: object.geometry, drawRange: object.geometry.drawRange, group: null, getBindings: () => [group] };
        objects.set(object, ro); ready.add(ro);
      }
      bundle.renderObjects.push(ro);
      renderer.info.render.drawCalls++; renderer.info.render.triangles += 12;
    }
  });
  return { renderer, context, pass, executed, objects, ready, get };
}

test('a bundle projects and records its members once, then replays them with no per-member projection', () => {
  const { renderer, context, pass, executed, objects, ready, get } = bundleRenderer();
  const scene = new THREE.Scene(), bundles = new StaticBundles(scene), t = tile();
  scene.add(t.model); bundles.add(t.model); bundles.update();
  const bundle = bundles.bundles.get(t.model)!;
  const camera = new THREE.PerspectiveCamera();
  const project = vi.spyOn(renderer, '_projectObject');
  const frame = () => {
    project.mockClear(); renderer._renderObjects.mockClear();
    const list = renderer._renderLists.get(scene, camera, renderer.lighting);
    list.begin(); renderer._projectObject(scene, camera, 0, list, context.clippingContext); list.finish();
    for (const entry of list.bundles) renderBundle(renderer, entry, scene, null, null);
    executeBundles(renderer);
    return list;
  };
  const members = [t.ground, t.road, t.wall, t.shadow, t.chunk];
  const projected = () => members.filter(o => project.mock.calls.some(call => call[0] === o)).length;
  const list = frame();
  expect(list.bundles.length).toBe(1);
  expect(projected()).toBe(members.length);
  expect(renderer._renderObjects).toHaveBeenCalledTimes(1);
  expect(pass.executeBundles).toHaveBeenCalledTimes(1);
  expect(get(context).currentSets).toEqual({ attributes: {}, bindingGroups: [], pipeline: null, index: null });
  // Replay: no projection or encoding per member; every recorded draw still refreshes, and still counts as drawn.
  const calls = renderer.info.render.drawCalls;
  frame();
  expect(projected()).toBe(0);
  expect(renderer._renderObjects).not.toHaveBeenCalled();
  expect(renderer._nodes.updateForRender).toHaveBeenCalledTimes(members.length);
  expect(renderer.info.render.drawCalls - calls).toBe(members.length);
  expect(renderer.info.render.triangles).toBe(members.length * 12 * 2);
  expect(executed.length).toBe(2);
  // A bind group replaced underneath (a texture swapped) records again before it executes.
  get(objects.get(t.road).getBindings()[0]).group = { id: 2 };
  frame();
  expect(renderer._renderObjects).toHaveBeenCalledTimes(1);
  frame(); expect(renderer._renderObjects).not.toHaveBeenCalled();
  // A pipeline still compiling at record time records again once it is ready, not before.
  bundle.needsUpdate = true; ready.delete(objects.get(t.wall));
  frame(); expect(renderer._renderObjects).toHaveBeenCalledTimes(1);
  frame(); expect(renderer._renderObjects).not.toHaveBeenCalled();
  ready.add(objects.get(t.wall));
  frame(); expect(renderer._renderObjects).toHaveBeenCalledTimes(1);
  // A culled group is skipped whole.
  bundle.visible = false; const culled = frame();
  expect(culled.bundles.length).toBe(0); expect(projected()).toBe(0);
  bundles.dispose();
});

test('recording restores an outer bundle, so a nested render can neither join nor end it', () => {
  const { renderer } = bundleRenderer();
  const outer = { outer: true };
  renderer._currentRenderBundle = outer;
  const group = new THREE.BundleGroup(); group.add(surface());
  const list = renderer._renderLists.get(group, new THREE.PerspectiveCamera(), renderer.lighting);
  list.begin(); list.push(group.children[0], (group.children[0] as THREE.Mesh).geometry, (group.children[0] as THREE.Mesh).material, 0, 0, null, null); list.finish();
  renderBundle(renderer, { bundleGroup: group as never, camera: new THREE.PerspectiveCamera(), renderList: list }, new THREE.Scene(), null, null);
  expect(renderer._currentRenderBundle).toBe(outer);
});
