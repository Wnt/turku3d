import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { facadeKitMaterial } from '../../src/render/facadeKit';
import { GameRenderer } from '../../src/render/shadowMaterials';

type ShaderBuilder = NodeBuilder & { scene: THREE.Scene; camera: THREE.Camera; build(): void; vertexShader: string; fragmentShader: string };
const fields = { _kit_layout: [42, 3, 4], _kit_origin: [0, 0], _kit_extent: [0, 0, 12, 16],
  _kit_floors: [4, 4, 1], _kit_wall: [.8, .7, .6], _kit_trim: [1, 1, 1],
  _kit_opening: [.45, .5, .2, .1], _kit_detail: [0, 0, 2, -3], _kit_bands: [.5, .2, .1, .2], _kit_base: [.7, .6, .5] };

test.each([false, true])('facade program constants use flat varyings while UVs remain interpolated (WebGL=%s)', webgl => {
  const renderer = new GameRenderer({ forceWebGL: webgl, canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false;
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1, 0, 0, 1], 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1], 2));
  for (const [name, values] of Object.entries(fields)) geometry.setAttribute(name,
    new THREE.Float32BufferAttribute([...values, ...values, ...values], values.length));
  const atlas = new THREE.DataTexture(new Uint8Array(4), 1, 1);
  const source = new THREE.MeshStandardMaterial({ map: atlas, normalMap: atlas, roughnessMap: atlas });
  source.userData.facade_kit = { version: 4 };
  const material = facadeKitMaterial(source, renderer)!;
  const mesh = new THREE.Mesh(geometry, material);
  const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(object: THREE.Object3D, renderer: THREE.WebGPURenderer): ShaderBuilder };
  const builder = backend.createNodeBuilder(mesh, renderer);
  builder.scene = new THREE.Scene(); builder.camera = new THREE.PerspectiveCamera(); builder.build();
  for (const name of Object.keys(fields).filter(name => name !== '_kit_origin')) {
    const declaration = webgl ? new RegExp(`flat\\s+(?:in|out) vec[234] v${name}\\b`)
      : new RegExp(`@interpolate\\(\\s*flat\\s*\\) v${name}\\b`);
    expect(declaration.test(builder.vertexShader), name + ' vertex').toBe(true);
    expect(declaration.test(builder.fragmentShader), name + ' fragment').toBe(true);
  }
  const uvVarying = builder.vertexShader.match(/(\w+) = uv;/)?.[1];
  expect(uvVarying).toBeTruthy();
  const uvDeclarations = builder.fragmentShader.split('\n').filter(line =>
    line.includes(uvVarying!) && (webgl ? /in vec2/.test(line) : /@location/.test(line)));
  expect(uvDeclarations).toHaveLength(1);
  expect(uvDeclarations[0]).not.toMatch(/flat/);
});
