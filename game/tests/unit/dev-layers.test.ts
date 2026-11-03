import { afterEach, expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { DevLayers, layerPath } from '../../src/ui/devLayers';
import { DevLayerRanges } from '../../src/ui/devLayerRanges';

function element(): any {
  return { append() {}, replaceChildren() {}, setAttribute() {}, querySelectorAll: () => [], style: {}, dataset: {}, remove() {} };
}
afterEach(() => vi.unstubAllGlobals());
function setup(choices: Record<string, boolean> = {}, readout: any = null) {
  vi.stubGlobal('location', { hostname: 'localhost', search: '' });
  const events = new EventTarget();
  vi.stubGlobal('document', { createElement: element, createTextNode: () => ({}), body: element(), getElementById: () => readout,
    addEventListener: events.addEventListener.bind(events), removeEventListener: events.removeEventListener.bind(events) });
  vi.stubGlobal('localStorage', { getItem: () => JSON.stringify({ choices }), setItem() {} });
  const scene = new THREE.Scene();
  return { scene, panel: new DevLayers(scene, null), events };
}

test('closed layer button follows HUD visibility, resized text and debug layout without repeated measurements', () => {
  let resize = () => {};
  const observe = vi.fn(), disconnect = vi.fn();
  vi.stubGlobal('ResizeObserver', class { constructor(callback: () => void) { resize = callback; } observe = observe; disconnect = disconnect; });
  let bottom = 140;
  const readout = { hidden: false, getBoundingClientRect: vi.fn(() => ({ bottom })) };
  const { panel, events } = setup({}, readout);
  panel.update();
  expect(observe).toHaveBeenCalledWith(readout);
  expect(panel.element.style.top).toBe('144px');
  panel.update(); panel.update();
  expect(readout.getBoundingClientRect).toHaveBeenCalledOnce();
  readout.hidden = true; panel.update();
  expect(panel.element.style.top).toBe('86px');
  readout.hidden = false; panel.update();
  expect(panel.element.style.top).toBe('144px');
  bottom = 168; resize(); panel.update();
  expect(panel.element.style.top).toBe('172px');
  bottom = 294; events.dispatchEvent(new Event('turku3d-hud-layout')); panel.update();
  expect(panel.element.style.top).toBe('298px');
  panel.dispose(); expect(disconnect).toHaveBeenCalledOnce();
});
test('discovers authored types, tree variants, bridge parts and unfamiliar layers', () => {
  expect(layerPath({ layer: 'buildings', surface: 'facade-component' })).toBe('Buildings/Facade Component');
  expect(layerPath({ layer: 'vegetation', variant: 'birch', material_key: 'tree_bark' })).toBe('Vegetation/Birch');
  expect(layerPath({ layer: 'bridges', kind: 'prop', object_id: 'bridge:Auransilta:rail:0:1' })).toBe('Structures/Rail');
  expect(layerPath({ layer: 'future_layer', kind: 'lantern' })).toBe('Future Layer/Lantern');
});
test('mixed ranges keep private buffers while filtering and release them when all layers are allowed', () => {
  const { scene, panel } = setup({ 'Buildings/Roof': false });
  const original = new THREE.BoxGeometry();
  const indices = Array.from(original.index!.array);
  const mesh = new THREE.Mesh(original, new THREE.MeshStandardMaterial());
  mesh.userData = { layer: 'buildings', objects: [
    { surface: 'roof', layer: 'buildings', start: 0, count: 6 },
    { surface: 'wall', layer: 'buildings', start: 6, count: 30 },
  ] };
  scene.add(mesh); panel.update();
  const filtered = mesh.geometry, dispose = vi.spyOn(filtered, 'dispose');
  expect(filtered.attributes.position).not.toBe(original.attributes.position);
  expect(filtered.attributes.position.array).not.toBe(original.attributes.position.array);
  expect(filtered.groups.map(g => g.count)).toEqual([6, 30]);
  const materials = (mesh as THREE.Mesh).material as THREE.Material[];
  expect(materials.map(m => m.visible)).toEqual([false, true]);
  expect(Array.from(original.index!.array)).toEqual(indices);
  for (let i = 0; i < 10; i++) {
    panel.restore(); expect(mesh.geometry).toBe(original);
    panel.update();
    expect(mesh.geometry).toBe(filtered);
    expect(mesh.material).toBe(materials);
    expect(materials[0].visible).toBe(false);
  }
  expect(dispose).not.toHaveBeenCalled();
  panel.restore();
  (panel as any).choices['Buildings/Roof'] = true;
  panel.update();
  expect(mesh.geometry).toBe(original);
  expect(dispose).toHaveBeenCalledOnce();
  expect(Array.isArray(mesh.material)).toBe(false);
  panel.dispose();
});

test('range buckets preserve clipped triangle order and material ownership without touching source indices', () => {
  const geometry = new THREE.BoxGeometry();
  geometry.clearGroups(); geometry.addGroup(0, 18, 0); geometry.addGroup(18, 18, 1);
  geometry.setDrawRange(3, 27);
  const source = Array.from(geometry.index!.array);
  const materials = [new THREE.MeshStandardMaterial(), new THREE.MeshStandardMaterial()];
  const mesh = new THREE.Mesh(geometry, materials);
  const rows = [{ start: 0, count: 6 }, { start: 6, count: 6 }, { start: 12, count: 6 },
    { start: 18, count: 6 }, { start: 24, count: 12 }];
  const ranges = new DevLayerRanges(mesh, rows, ['roof', 'wall', 'roof', 'roof', 'wall']);
  const expected = [...source.slice(3, 6), ...source.slice(12, 18), ...source.slice(6, 12),
    ...source.slice(18, 24), ...source.slice(24, 30)];
  expect(Array.from(ranges.geometry.index!.array)).toEqual(expected);
  expect(ranges.geometry.groups.map(g => [g.start, g.count, g.materialIndex])).toEqual([
    [0, 9, 0], [9, 6, 1], [15, 6, 2], [21, 6, 3],
  ]);
  expect(Array.from(geometry.index!.array)).toEqual(source);
  ranges.apply(path => path !== 'roof');
  expect((mesh.material as THREE.Material[]).map(m => m.visible)).toEqual([false, true, false, true]);
  ranges.restore(); ranges.dispose();
  expect(mesh.geometry).toBe(geometry);
});

test.each([65536, 65537])('nonindexed filtering preserves high vertex indices for %s vertices', count => {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 3), 3));
  const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
  const ranges = new DevLayerRanges(mesh, [{ start: count - 3, count: 3 }], ['wall']);
  expect(Array.from(ranges.geometry.index!.array)).toEqual([count - 3, count - 2, count - 1]);
  expect(ranges.geometry.index!.array).toBeInstanceOf(count > 65536 ? Uint32Array : Uint16Array);
  expect(geometry.index).toBeNull();
  ranges.dispose();
});

test('closed default panel neither traverses nor clones mixed batch geometry', () => {
  const { scene, panel } = setup();
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
  mesh.userData.objects = [
    { layer: 'buildings', surface: 'roof', start: 0, count: 6 },
    { layer: 'buildings', surface: 'wall', start: 6, count: 30 },
  ];
  scene.add(mesh);
  const clone = vi.spyOn(mesh.geometry, 'clone'), traverse = vi.spyOn(scene, 'traverse');
  panel.update(); panel.restore(); panel.update();
  expect(traverse).not.toHaveBeenCalled();
  expect(clone).not.toHaveBeenCalled();
  panel.toggle(); panel.update(); panel.update();
  expect(traverse).toHaveBeenCalledTimes(1);
  const next = new THREE.Mesh(); next.userData = { layer: 'new_layer' }; scene.add(next);
  panel.update();
  expect(traverse).toHaveBeenCalledTimes(2);
  scene.remove(mesh);
  expect((panel as any).observed.has(mesh)).toBe(false);
  expect((panel as any).entries).toHaveLength(0);
  panel.dispose();
});

test('removing a filtered batch releases private geometry while the panel stays closed', () => {
  const { scene, panel } = setup({ 'Buildings/Roof': false });
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
  mesh.userData.objects = [
    { layer: 'buildings', surface: 'roof', start: 0, count: 6 },
    { layer: 'buildings', surface: 'wall', start: 6, count: 30 },
  ];
  scene.add(mesh); panel.update();
  const dispose = vi.spyOn(mesh.geometry, 'dispose');
  panel.restore(); scene.remove(mesh);
  expect(dispose).toHaveBeenCalledOnce();
  expect((panel as any).filtered.size).toBe(0);
  panel.dispose();
});
test('hiding an entire merged batch masks the draw without an empty replacement index', () => {
  const { scene, panel } = setup({ Buildings: false });
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
  const original = mesh.geometry;
  mesh.userData.objects = [{ layer: 'buildings', surface: 'roof', start: 0, count: 36 }];
  scene.add(mesh); panel.update();
  expect(mesh.geometry).toBe(original); expect(mesh.geometry.index!.count).toBe(36);
  expect(mesh.layers.mask).toBe(0);
  panel.restore(); expect(mesh.layers.mask).toBe(1);
  panel.dispose();
});
test('parent choices apply to future children and restore runtime camera masks each frame', () => {
  const { scene, panel } = setup({ Vegetation: false });
  const tree = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
  tree.userData = { layer: 'vegetation', variant: 'new_species' }; tree.layers.enable(2); scene.add(tree);
  const mask = tree.layers.mask;
  panel.update(); expect(tree.layers.mask).toBe(0);
  panel.restore(); expect(tree.layers.mask).toBe(mask);
  expect(tree.visible).toBe(true);
  panel.dispose();
});

test('live screenshot direction, position and FOV follow the camera while the panel is closed', () => {
  const { scene } = setup();
  const setView = vi.fn();
  const coarse = { screenshotScenery: { radius: 6000, setView, progress: {} }, setVisualPass() {} };
  const camera = new THREE.PerspectiveCamera(60, 1.6, .1, 10000);
  const panel = new DevLayers(scene, coarse as never, undefined, undefined, camera);
  camera.position.set(3751, 164, 430); camera.rotation.set(0, Math.PI / 2, 0); camera.updateMatrixWorld();
  panel.update();
  expect(setView.mock.calls.at(-1)![0]).toMatchObject({ x: 3751, y: 164, z: 430, dx: -1 });
  camera.position.x += 300; camera.rotation.y = -Math.PI / 2; camera.fov = 90; camera.updateMatrixWorld();
  panel.update();
  const view = setView.mock.calls.at(-1)![0];
  expect(view.x).toBe(4051); expect(view.dx).toBeCloseTo(1);
  expect(view.halfAngle).toBeCloseTo(Math.atan(1.6) + .15);
});
