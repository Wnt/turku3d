import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
// @ts-expect-error r186 render-object internals have no DefinitelyTyped declarations.
import RenderObjects from 'three/src/renderers/common/RenderObjects.js';
// @ts-expect-error r186 render-object internals have no DefinitelyTyped declarations.
import RenderObject from 'three/src/renderers/common/RenderObject.js';
import { NormalPrepass } from '../../src/render/normalPrepass';
import { GameRenderer, ShadowMaterials, hasDrawVertices, shadowReadsAlpha } from '../../src/render/shadowMaterials';
import { bool, positionLocal } from 'three/tsl';

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
  Object.assign(renderer, { shadowMaterials: new ShadowMaterials(), shadowPasses: new Map(), contextNode: { id: 1, version: 0 }, shadowContext: { id: 1, version: 0 },
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

test('casters whose shadow reads no alpha keep only their position and depth nodes: one shadow program for all', () => {
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const own = (renderer as unknown as { _getShadowNodes(m: THREE.Material): { colorNode: unknown; positionNode: unknown; depthNode: unknown } });
  const base = (THREE.WebGPURenderer.prototype as unknown as typeof own)._getShadowNodes;
  const [a, b] = [new THREE.Texture(), new THREE.Texture()].map(map => new THREE.MeshStandardNodeMaterial({ map }));
  // r186: an alpha node bound to each material (node ids are in the program key).
  expect(base.call(renderer, a).colorNode).not.toBeNull();
  expect(base.call(renderer, a).colorNode).not.toBe(base.call(renderer, b).colorNode);
  expect(own._getShadowNodes(a).colorNode).toBeNull();
  expect(own._getShadowNodes(a)).toBe(own._getShadowNodes(a));
  // A vertex program still moves the shadow (walkers' VAT, wind).
  const moved = new THREE.MeshStandardNodeMaterial({ map: new THREE.Texture() });
  moved.positionNode = positionLocal.add(1);
  expect(own._getShadowNodes(moved).positionNode).toBe(moved.positionNode);
  expect(own._getShadowNodes(moved).colorNode).toBeNull();
  // Cutouts, masks and dissolve twins discard by alpha: r186's nodes stay.
  const cutout = new THREE.MeshStandardNodeMaterial({ map: new THREE.Texture(), alphaTest: 0.5 });
  expect(own._getShadowNodes(cutout).colorNode).not.toBeNull();
  const masked = new THREE.MeshStandardNodeMaterial(); masked.maskShadowNode = bool(true);
  expect(own._getShadowNodes(masked).colorNode).not.toBeNull();
  expect(shadowReadsAlpha(a)).toBe(false);
  expect(shadowReadsAlpha(cutout)).toBe(true);
});
