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
test('filters mixed merged ranges without mutating source indices, restores ownership on disposal', () => {
  const { scene, panel } = setup({ 'Buildings/Roof': false });
  const original = new THREE.BoxGeometry();
  const indices = Array.from(original.index!.array);
  const mesh = new THREE.Mesh(original, new THREE.MeshStandardMaterial());
  mesh.userData = { layer: 'buildings', objects: [
    { surface: 'roof', layer: 'buildings', start: 0, count: 6 },
    { surface: 'wall', layer: 'buildings', start: 6, count: 30 },
  ] };
  scene.add(mesh); panel.update();
  expect(mesh.geometry.index!.count).toBe(30);
  expect(Array.from(mesh.geometry.index!.array)).toEqual(indices.slice(6));
  expect(Array.from(original.index!.array)).toEqual(indices);
  mesh.dispatchEvent({ type: 'dispose' } as never);
  expect(mesh.geometry).toBe(original);
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
