import { afterEach, expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { DevLayers, layerPath } from '../../src/ui/devLayers';

function element(): any {
  return { append() {}, replaceChildren() {}, setAttribute() {}, querySelectorAll: () => [], style: {}, dataset: {}, remove() {} };
}
afterEach(() => vi.unstubAllGlobals());
function setup(choices: Record<string, boolean> = {}) {
  vi.stubGlobal('location', { hostname: 'localhost', search: '' });
  vi.stubGlobal('document', { createElement: element, createTextNode: () => ({}), body: element(), getElementById: () => null });
  vi.stubGlobal('localStorage', { getItem: () => JSON.stringify({ choices }), setItem() {} });
  const scene = new THREE.Scene();
  return { scene, panel: new DevLayers(scene, null) };
}
test('discovers authored types, tree variants, bridge parts and unfamiliar layers', () => {
  expect(layerPath({ layer: 'buildings', surface: 'facade-component' })).toBe('Buildings/Facade Component');
  expect(layerPath({ layer: 'vegetation', variant: 'birch', material_key: 'tree_bark' })).toBe('Vegetation/Birch');
  expect(layerPath({ layer: 'bridges', kind: 'prop', object_id: 'bridge:Auransilta:rail:0:1' })).toBe('Structures/Rail');
  expect(layerPath({ layer: 'future_layer', kind: 'lantern' })).toBe('Future Layer/Lantern');
});
test('mixed ranges keep private buffers across repeated toggles and restore source ownership', () => {
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
    (panel as any).choices['Buildings/Roof'] = i % 2 === 0;
    panel.update();
    expect(mesh.geometry).toBe(filtered);
    expect(((mesh as THREE.Mesh).material as THREE.Material[])[0]).toBe(materials[0]);
    expect(materials[0].visible).toBe(i % 2 === 0);
  }
  expect(dispose).not.toHaveBeenCalled();
  mesh.dispatchEvent({ type: 'dispose' } as never);
  expect(mesh.geometry).toBe(original);
  expect(dispose).toHaveBeenCalledOnce();
  expect(Array.isArray(mesh.material)).toBe(false);
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
