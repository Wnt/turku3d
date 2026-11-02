import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
// @ts-expect-error r186 render-object internals have no DefinitelyTyped declarations.
import RenderObjects from 'three/src/renderers/common/RenderObjects.js';
// @ts-expect-error r186 render-object internals have no DefinitelyTyped declarations.
import RenderObject from 'three/src/renderers/common/RenderObject.js';
import { NormalPrepass } from '../../src/render/normalPrepass';
import { GameRenderer, ShadowMaterials, hasDrawVertices } from '../../src/render/shadowMaterials';

test('empty geometry, groups and disjoint draw ranges are skipped before pass encoding', () => {
  const geometry = new THREE.BufferGeometry();
  expect(hasDrawVertices(geometry, null)).toBe(false);
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(9), 3));
  geometry.setIndex([]); expect(hasDrawVertices(geometry, null)).toBe(false);
  geometry.setIndex([0, 1, 2]); expect(hasDrawVertices(geometry, null)).toBe(true);
  expect(hasDrawVertices(geometry, { start: 0, count: 0 })).toBe(false);
  geometry.setDrawRange(3, 3); expect(hasDrawVertices(geometry, null)).toBe(false);
  geometry.setDrawRange(0, 3); expect(hasDrawVertices(geometry, { start: 3, count: 3 })).toBe(false);
  geometry.setIndex(null); expect(hasDrawVertices(geometry, null)).toBe(true);
});

test('opaque/cutout alternation computes shadow program keys once per caster', () => {
  // Exercise the actual r186 override and render-object cache paths without a GPU or renderer initialization.
  const renderer = Object.create(GameRenderer.prototype) as GameRenderer;
  const context = { id: 1 };
  const nodes = { getCacheKey: () => 1 };
  Object.assign(renderer, { shadowMaterials: new ShadowMaterials(), contextNode: { id: 1, version: 0 }, shadowContext: { id: 1, version: 0 },
    backend: {}, shadowMap: { type: THREE.PCFShadowMap }, _currentSourceMaterial: null,
    _getShadowNodes: () => ({ colorNode: null, depthNode: null, positionNode: null }) });
  const objects = new RenderObjects(renderer, nodes as never, {} as never, {} as never, {} as never, {} as never);
  Object.assign(renderer, { _handleObjectFunction: (mesh: THREE.Mesh, material: THREE.Material, scene: THREE.Scene,
    camera: THREE.Camera, lights: never) => objects.get(mesh, material, scene, camera, lights, context as never, null) });
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera();
  const override = new THREE.NodeMaterial() as THREE.NodeMaterial & { isShadowPassMaterial: boolean };
  override.isShadowPassMaterial = true; scene.overrideMaterial = override;
  const opaque = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial());
  const masked = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial({ alphaTest: 0.5 }));
  const lights = {} as never;
  const keys = vi.spyOn(RenderObject.prototype, 'getMaterialCacheKey');
  try {
    for (let frame = 0; frame < 60; frame++) for (const mesh of [opaque, masked]) {
      const context = { id: frame % 2 + 1, version: 0 };
      Object.assign(renderer, { contextNode: context });
      renderer.renderObject(mesh, scene, camera, mesh.geometry, mesh.material, null, lights);
      expect(scene.overrideMaterial).toBe(override);
      expect(renderer.contextNode).toBe(context);
    }
    expect(keys).toHaveBeenCalledTimes(2);
    expect(override.version).toBe(0);
  } finally { keys.mockRestore(); }
});

test('shadow variants follow explicit invalidation and dispose with their template', () => {
  const variants = new ShadowMaterials();
  const base = new THREE.NodeMaterial() as THREE.NodeMaterial & { isShadowPassMaterial: boolean };
  base.isShadowPassMaterial = true;
  const source = new THREE.MeshBasicMaterial({ alphaTest: 0.5 });
  const masked = variants.select(source, base)!;
  expect(masked).not.toBe(base);
  expect(variants.select(source, base)).toBe(masked);
  base.needsUpdate = true; base.side = THREE.DoubleSide;
  expect(variants.select(source, base)!.side).toBe(THREE.DoubleSide);
  const disposed = vi.fn(); masked.addEventListener('dispose', disposed);
  base.dispose(); expect(disposed).toHaveBeenCalledOnce();
  expect(variants.select(source, null)).toBeNull();
  expect(variants.select(source, source)).toBe(source);
});

test('normal alpha identifies wet minerals across alternating receivers and authored cutouts', () => {
  const prepass = new NormalPrepass();
  const road = new THREE.MeshStandardNodeMaterial(), grass = new THREE.MeshStandardNodeMaterial();
  road.userData.weatherWetness = true;
  const wet = prepass.select(road)!, dry = prepass.select(grass)!;
  expect(wet).not.toBe(dry);
  for (let i = 0; i < 10; i++) {
    expect(prepass.select(road)).toBe(wet);
    expect(prepass.select(grass)).toBe(dry);
    expect(wet.userData.weatherReceiver).toBe(1);
    expect(dry.userData.weatherReceiver).toBe(0);
  }
  const leaf = new THREE.MeshStandardNodeMaterial({ alphaTest: .5 });
  expect(prepass.select(leaf)).toBeNull();
  expect(leaf.userData.weatherReceiver).toBe(0);
  prepass.dispose();
});
