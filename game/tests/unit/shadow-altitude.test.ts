import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { AltitudeShadows, altitudeShadows, smallCaster } from '../../src/render/shadowAltitude';
import { GameRenderer, ShadowMaterials } from '../../src/render/shadowMaterials';

const mesh = (radius: number) => new THREE.Mesh(new THREE.SphereGeometry(radius), new THREE.MeshBasicMaterial());

test('small casters: vegetation, facade relief, props and anything under 6 m; buildings and masts keep casting', () => {
  expect(smallCaster(mesh(2.5))).toBe(true); // a car, a walker, a sign
  expect(smallCaster(mesh(40))).toBe(false); // a building's shadow chunk
  const tree = mesh(9); tree.userData.nature = 'tree_oak'; expect(smallCaster(tree)).toBe(true);
  const relief = mesh(30); relief.userData.facadeModules = true; expect(smallCaster(relief)).toBe(true);
  const sign = mesh(30); sign.userData.objects = [{ prop_type: 'sign' }]; expect(smallCaster(sign)).toBe(true);
  const owner = new THREE.Group(); owner.userData.layer = 'vegetation'; const bush = mesh(20); owner.add(bush);
  expect(smallCaster(bush)).toBe(true);
  // A batch of small instances is small, whatever the batch's own extent; a floodlight mast is not.
  const lamps = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.2, 0.2, 9), new THREE.MeshBasicMaterial(), 200);
  expect(smallCaster(lamps)).toBe(true);
  expect(smallCaster(new THREE.InstancedMesh(new THREE.CylinderGeometry(0.5, 0.5, 30), new THREE.MeshBasicMaterial(), 4))).toBe(false);
  const scaled = mesh(2); scaled.scale.setScalar(5); scaled.updateMatrixWorld(); expect(smallCaster(scaled)).toBe(false);
});

test('the rule switches on above 100 m and off below 90 m, and only when enabled', () => {
  const rule = new AltitudeShadows(), car = mesh(2), house = mesh(30);
  for (const [height, high] of [[50, false], [95, false], [101, true], [95, true], [89, false], [NaN, false]] as const) {
    rule.update(height); expect(rule.high).toBe(high);
    expect(rule.skips(car)).toBe(high); expect(rule.skips(house)).toBe(false);
  }
  rule.enabled = false; rule.update(500); expect(rule.skips(car)).toBe(false);
});

test('a high camera skips small casters in sun-shadow passes only, never in preparation or other passes', () => {
  const renderer = Object.create(GameRenderer.prototype) as GameRenderer;
  const drawn: THREE.Object3D[] = [];
  Object.assign(renderer, { shadowMaterials: new ShadowMaterials(), shadowPasses: new Map(), contextNode: { id: 1 }, shadowContext: { id: 2 },
    backend: {}, _currentRenderBundle: null, getRenderTarget: () => null, getMRT: () => null, _callDepth: 3 });
  const base = Object.getPrototypeOf(GameRenderer.prototype) as { renderObject: (...args: unknown[]) => void };
  const spy = vi.spyOn(base, 'renderObject').mockImplementation(function (object) { drawn.push(object as THREE.Object3D); });
  try {
    const scene = new THREE.Scene(), camera = new THREE.OrthographicCamera(), car = mesh(2), house = mesh(30);
    const shadow = new THREE.MeshBasicMaterial() as THREE.MeshBasicMaterial & { isShadowPassMaterial: boolean };
    shadow.isShadowPassMaterial = true;
    const draw = (object: THREE.Mesh, override: THREE.Material | null) => {
      scene.overrideMaterial = override;
      renderer.renderObject(object, scene, camera, object.geometry, object.material as THREE.Material, null, {} as never);
    };
    altitudeShadows.update(150);
    draw(car, shadow); draw(house, shadow); draw(car, null);
    expect(drawn).toEqual([house, car]);
    expect(renderer.contextNode).toEqual({ id: 1 });
    Object.assign(renderer, { _isPreCompiling: true }); drawn.length = 0;
    draw(car, shadow); expect(drawn).toEqual([car]);
    Object.assign(renderer, { _isPreCompiling: false }); drawn.length = 0;
    altitudeShadows.update(20);
    draw(car, shadow); expect(drawn).toEqual([car]);
  } finally { spy.mockRestore(); altitudeShadows.update(0); }
});
