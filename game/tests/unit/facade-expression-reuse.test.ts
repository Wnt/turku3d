import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import type Node from 'three/src/nodes/core/Node.js';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import * as program from '../../src/render/facadeProgram';
import { facadeKitMaterial } from '../../src/render/facadeKit';
import { facadeProgramSurface } from '../../src/render/facadeProgramSurface';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { nightUniforms } from '../../src/render/nightLights';

function material(version = 5, webgl = false) {
  const renderer = new GameRenderer({ forceWebGL: webgl, canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false;
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  const map = new THREE.DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1);
  const source = new THREE.MeshStandardMaterial({ map, normalMap: map, roughnessMap: map });
  source.userData.facade_kit = { version };
  return { renderer, material: facadeKitMaterial(source, renderer)! };
}
function expressions(material: THREE.MeshStandardNodeMaterial) {
  const seen = new Set<Node>(), todo: Node[] = [];
  for (const value of Object.values(material)) if (value?.isNode) todo.push(value);
  while (todo.length) {
    const node = todo.pop()!;
    if (seen.has(node)) continue;
    seen.add(node);
    for (const child of node.getChildren()) todo.push(child);
  }
  return seen.size;
}

test.each([4, 5])('facade v%s constructs its opening and pigment programs only as needed', version => {
  const layout = vi.spyOn(program, 'programLayout'), pigment = vi.spyOn(program, 'programPigment');
  try {
    material(version);
    expect(layout).toHaveBeenCalledTimes(version >= 5 ? 1 : 2);
    expect(pigment).toHaveBeenCalledTimes(version >= 5 ? 1 : 2);
  } finally { layout.mockRestore(); pigment.mockRestore(); }
});

test('v5 emission reuses the existing expression graph instead of retaining a duplicate layout', () => {
  const { material: facade } = material();
  const shared = expressions(facade);
  facade.emissiveNode = facadeProgramSurface(5).emission;
  expect(shared).toBeLessThan(expressions(facade));
});

test.each([false, true])('v5 day and night retain one program with packed paint and frame attributes (WebGL=%s)', webgl => {
  const { renderer, material: facade } = material(5, webgl);
  const fields = { _kit_layout: [7, 3, 4], _kit_extent: [0, 0, 12, 16], _kit_floors: [4, 4, 1],
    _kit_wall: [.8, .7, .6], _kit_trim: [1, 1, 1], _kit_opening: [.45, .5, .2, .1],
    _kit_detail: [0, 0, 2, -3], _kit_bands: [.5, .2, .1, .2], _kit_base: [.7, .6, .5],
    _kit_frame: [.2, .3, .4, .1], _kit_glazing: [.06, .06, 1, -1] };
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1, 0, 0, 1], 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1], 2));
  for (const [name, values] of Object.entries(fields)) geometry.setAttribute(name,
    new THREE.Float32BufferAttribute([...values, ...values, ...values], values.length));
  const mesh = new THREE.Mesh(geometry, facade);
  type Builder = NodeBuilder & { scene: THREE.Scene; camera: THREE.Camera; build(): void; vertexShader: string; fragmentShader: string };
  const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(object: THREE.Object3D, renderer: THREE.WebGPURenderer): Builder };
  const build = () => {
    const builder = backend.createNodeBuilder(mesh, renderer);
    builder.scene = new THREE.Scene(); builder.camera = new THREE.PerspectiveCamera(); builder.build();
    return { vertex: builder.vertexShader, fragment: builder.fragmentShader };
  };
  const night = nightUniforms.night.value, hour = nightUniforms.hour.value;
  try {
    nightUniforms.night.value = 0; nightUniforms.hour.value = 13;
    const day = build(), key = facade.customProgramCacheKey();
    nightUniforms.night.value = 1; nightUniforms.hour.value = 23.5;
    expect(build()).toEqual(day);
    expect(facade.customProgramCacheKey()).toBe(key);
  } finally { nightUniforms.night.value = night; nightUniforms.hour.value = hour; }
});
