import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { mrt, output, vec4 } from 'three/tsl';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { guardFrame } from '../../src/render/frameGuard';

type Builder = NodeBuilder & { scene: THREE.Scene; camera: THREE.Camera; build(): void; fragmentShader: string };
type Internals = { _callDepth: number; _mrt: unknown; _renderObjectFunction: unknown };

function renderer() {
  const r = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  r.hasFeature = () => false; Object.assign(r.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  return r;
}

/** Beauty's target and MRT (render/post.ts, no prepass), set the way r186's PassNode sets them around its render. */
function beautyPass() {
  const target = new THREE.RenderTarget(4, 4, { count: 3 });
  target.textures.forEach((texture, i) => { texture.name = ['output', 'normal', 'velocity'][i]; });
  return { target, mrt: mrt({ output, normal: vec4(0), velocity: vec4(0) }) };
}

/** The fragment program a world material gets with the renderer's current pass state, as a deferred build sees it. */
function fragment(r: THREE.WebGPURenderer, scene: THREE.Scene) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardNodeMaterial());
  const backend = r.backend as typeof r.backend & { createNodeBuilder(o: THREE.Object3D, r: THREE.WebGPURenderer): Builder };
  const builder = backend.createNodeBuilder(mesh, r);
  builder.scene = scene; builder.camera = new THREE.PerspectiveCamera(); builder.build();
  return builder.fragmentShader;
}

const EMPTY_OUTPUT = /struct OutputType \{\s*\}/;

test('a frame that throws inside a pass leaves no pass target, MRT or override on the renderer', () => {
  const r = renderer(), scene = new THREE.Scene(), internal = r as unknown as Internals, { target, mrt: passMrt } = beautyPass();
  const override = new THREE.MeshBasicNodeMaterial(), depth = internal._callDepth;
  r.setClearColor(0x336699, 1);
  expect(() => guardFrame(r, scene, () => {
    // PassNode.updateBefore, then Renderer._renderScene and a shadow cascade, interrupted by one failed buffer write.
    r.setRenderTarget(target); r.setMRT(passMrt); internal._callDepth += 2;
    scene.overrideMaterial = override; r.setRenderObjectFunction(() => {}); r.setClearColor(0x000000, 0);
    throw new TypeError("Failed to execute 'writeBuffer' on 'GPUQueue': parameter 1 is not of type 'GPUBuffer'.");
  })).toThrow(/writeBuffer/);
  expect(r.getRenderTarget()).toBeNull();
  expect(r.getMRT()).toBeNull();
  expect(internal._callDepth).toBe(depth);
  expect(scene.overrideMaterial).toBeNull();
  expect(internal._renderObjectFunction).toBeNull();
  expect(r.getClearAlpha()).toBe(1);
  expect(r.getClearColor(new THREE.Color()).getHex()).toBe(new THREE.Color(0x336699).getHex());
});

test('after a failed frame, programs built for another target still have colour outputs', () => {
  const r = renderer(), scene = new THREE.Scene(), { target, mrt: passMrt } = beautyPass();
  // The RenderPipeline's output quad and post effects draw into targets of their own (one unnamed texture).
  const effect = new THREE.RenderTarget(4, 4);
  // Without the guard: beauty's MRT stays on the renderer, and a program for any other target has no output member.
  r.setMRT(passMrt); r.setRenderTarget(effect);
  expect(fragment(r, scene)).toMatch(EMPTY_OUTPUT);
  r.setMRT(null); r.setRenderTarget(null);
  try { guardFrame(r, scene, () => { r.setRenderTarget(target); r.setMRT(passMrt); throw new Error('failed write'); }); } catch { /* the frame's own error */ }
  r.setRenderTarget(effect);
  expect(fragment(r, scene)).not.toMatch(EMPTY_OUTPUT);
});
