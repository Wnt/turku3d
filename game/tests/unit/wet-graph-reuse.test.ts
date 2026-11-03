import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { float, vec3 } from 'three/tsl';
// @ts-expect-error r186 internal cache has no DefinitelyTyped declaration.
import NodeManager from 'three/src/renderers/common/nodes/NodeManager.js';
// @ts-expect-error r186 internal render object has no DefinitelyTyped declaration.
import RenderObject from 'three/src/renderers/common/RenderObject.js';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { MaterialStyler } from '../../src/render/materials';
import { wetSurface, weatherUniforms } from '../../src/render/weather/surfaces';

function fixture() {
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false;
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  const nodes = new NodeManager(renderer, renderer.backend), scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera();
  const lights = renderer.lighting.getNode(scene), context = { id: 1 };
  return { renderer, nodes, scene, draw(material: THREE.Material) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(), material);
    const draw = new RenderObject(nodes, {}, renderer, mesh, material, scene, camera, lights, context, null);
    draw.onDispose = () => nodes.delete(draw);
    return draw;
  } };
}

// Exact factors from real t0_1_0 stone, paint, tunnel floor and tunnel lamps.
const values = [[.36, .35, .32, .95], [.72, .71, .66, .9], [.22, .23, .24, .95], [1, .92, .65, .95]];

test('different plain PBR values share the actual r186 built state and retain per-draw material references', () => {
  const { nodes, draw } = fixture(), create = vi.spyOn(nodes, '_createNodeBuilder');
  const materials = values.map(([r, g, b, roughness]) => wetSurface(new THREE.MeshStandardNodeMaterial({ color: new THREE.Color(r, g, b), roughness })));
  const draws = materials.map(draw), states = draws.map(value => nodes.getForRender(value));
  expect(new Set(draws.map(value => value.initialCacheKey)).size).toBe(1);
  expect(new Set(states).size).toBe(1); expect(create).toHaveBeenCalledOnce();
  expect(new Set(materials).size).toBe(4);
  const a = draws[0].getBindings(), b = draws[1].getBindings();
  let independent = 0;
  for (let i = 0; i < a.length; i++) if (a[i].bindings[0].groupNode.shared !== true) {
    independent++; expect(a[i]).not.toBe(b[i]);
    for (let j = 0; j < a[i].bindings.length; j++) expect(a[i].bindings[j]).not.toBe(b[i].bindings[j]);
  }
  expect(independent).toBeGreaterThan(0);
  const references = states[0].updateNodes.filter((node: any) => node.isMaterialReferenceNode);
  const color = references.find((node: any) => node.property === 'color');
  const roughness = references.find((node: any) => node.property === 'roughness');
  expect(color).toBeDefined(); expect(roughness).toBeDefined();
  for (let i = 0; i < materials.length; i++) {
    const frame = { material: materials[i] };
    color.updateReference(frame); color.update(frame); roughness.updateReference(frame); roughness.update(frame);
    expect(color.node.value.toArray()).toEqual(values[i].slice(0, 3));
    expect(roughness.node.value).toBe(values[i][3]);
  }
  materials[0].color.setRGB(.11, .12, .13); materials[0].roughness = .33;
  for (const i of [0, 1, 0]) {
    const frame = { material: materials[i] };
    color.updateReference(frame); color.update(frame); roughness.updateReference(frame); roughness.update(frame);
    expect(color.node.value.toArray()).toEqual(i ? values[i].slice(0, 3) : [.11, .12, .13]);
    expect(roughness.node.value).toBe(i ? values[i][3] : .33);
  }
  for (const value of draws) value.dispose();
});

test('production styling reuses the recipe without pooling different source materials', () => {
  const { renderer } = fixture();
  const styler = new MaterialStyler(renderer, {} as never, 'medium', true);
  const meshes = values.map(([r, g, b, roughness]) => {
    const material = new THREE.MeshStandardMaterial({ color: new THREE.Color(r, g, b), roughness });
    material.userData.material_key = 'tunnel_concrete';
    const model = new THREE.Group(), mesh: THREE.Mesh = new THREE.Mesh(new THREE.BoxGeometry(), material); model.add(mesh); styler.apply(model);
    return mesh;
  });
  expect(new Set(meshes.map(mesh => mesh.material)).size).toBe(4);
  expect(new Set(meshes.map(mesh => (mesh.material as THREE.MeshStandardNodeMaterial).colorNode)).size).toBe(1);
  for (const mesh of meshes) { styler.release(mesh.parent!); mesh.geometry.dispose(); (mesh.material as THREE.Material).dispose(); }
});

test('authored textures, base expressions, mineral masks and facades keep independent graphs', () => {
  const common = wetSurface(new THREE.MeshStandardNodeMaterial());
  const variants = [
    () => new THREE.MeshStandardNodeMaterial({ map: new THREE.Texture() }),
    () => new THREE.MeshStandardNodeMaterial({ normalMap: new THREE.Texture() }),
    () => new THREE.MeshStandardNodeMaterial({ lightMap: new THREE.Texture() }),
    () => new THREE.MeshStandardNodeMaterial({ colorNode: vec3(.2, .4, .7) }),
    () => new THREE.MeshStandardNodeMaterial({ roughnessNode: float(.7) }),
    () => new THREE.MeshStandardNodeMaterial({ normalNode: vec3(0, 0, 1) }),
  ];
  for (const create of variants) {
    const a = wetSurface(create()), b = wetSurface(create());
    expect(a.colorNode).not.toBe(common.colorNode); expect(a.colorNode).not.toBe(b.colorNode);
  }
  expect(wetSurface(new THREE.MeshStandardNodeMaterial(), true).colorNode).not.toBe(common.colorNode);
  expect(wetSurface(new THREE.MeshStandardNodeMaterial(), false, float(.5)).colorNode).not.toBe(common.colorNode);
});

test('shared graph survives another material disposal, while idempotent application preserves the recipe', () => {
  const { nodes, draw } = fixture(), create = vi.spyOn(nodes, '_createNodeBuilder');
  const a = wetSurface(new THREE.MeshStandardNodeMaterial({ color: 0xff2200 }));
  const b = wetSurface(new THREE.MeshStandardNodeMaterial({ color: 0x0044ff }));
  const first = draw(a), second = draw(b), state = nodes.getForRender(first);
  expect(nodes.getForRender(second)).toBe(state); expect(state.usedTimes).toBe(2);
  a.dispose(); expect(state.usedTimes).toBe(1);
  expect(nodes.getForRender(second)).toBe(state); expect(create).toHaveBeenCalledOnce();
  const color = b.colorNode; expect(wetSurface(b)).toBe(b); expect(b.colorNode).toBe(color);
  b.dispose(); expect(state.usedTimes).toBe(0);
  const thirdMaterial = wetSurface(new THREE.MeshStandardNodeMaterial()), third = draw(thirdMaterial);
  expect(nodes.getForRender(third)).not.toBe(state); expect(create).toHaveBeenCalledTimes(2);
  thirdMaterial.dispose();
});

test.each([false, true])('dry/wet changes keep native shader code and program identity stable (WebGL=%s)', forceWebGL => {
  const renderer = new GameRenderer({ forceWebGL, canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false;
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  const material = wetSurface(new THREE.MeshStandardNodeMaterial()), mesh = new THREE.Mesh(new THREE.BoxGeometry(), material);
  const build = () => {
    const builder = (renderer.backend as any).createNodeBuilder(mesh, renderer);
    builder.scene = new THREE.Scene(); builder.camera = new THREE.PerspectiveCamera(); builder.build();
    return [builder.vertexShader, builder.fragmentShader];
  };
  const previous = weatherUniforms.wetness.value;
  try {
    weatherUniforms.wetness.value = 0;
    const dry = build(), key = material.customProgramCacheKey();
    weatherUniforms.wetness.value = .85;
    expect(build()).toEqual(dry); expect(material.customProgramCacheKey()).toBe(key);
  } finally { weatherUniforms.wetness.value = previous; }
});
