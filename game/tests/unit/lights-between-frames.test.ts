import './fleet-assets.fixture';
import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { ParkedCarVisuals } from '../../src/actors/car';
import { GameLighting } from '../../src/render/lighting';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { draftManifest } from '../../src/world/manifest';

type Builder = NodeBuilder & { scene: THREE.Scene; camera: THREE.Camera; lightsNode: THREE.LightsNode; build(): void; fragmentShader: string };

/** One r186 render of `scene` as Renderer._renderScene drives the lighting: save, collect lights, restore. */
function render(lighting: GameLighting, scene: THREE.Scene, lights: THREE.Light[]) {
  lighting.beginRender(scene);
  lighting.getNode(scene).setLights(lights); // RenderList.finish
  lighting.finishRender(scene);
}

test('the world keeps its lights between frames; a nested render still restores the outer set', () => {
  const lighting = new GameLighting({ point: 4 }), scene = new THREE.Scene();
  const sun = new THREE.DirectionalLight(), lamp = new THREE.PointLight();
  // r186 restored the empty set the lights node started with after every frame.
  render(lighting, scene, [sun, lamp]);
  expect(lighting.getNode(scene).getLights()).toEqual([sun, lamp]);
  // A nested pass with fewer lights (inside the frame) gives the outer pass its own set back.
  lighting.beginRender(scene);
  lighting.getNode(scene).setLights([sun, lamp]);
  render(lighting, scene, [sun]);
  expect(lighting.getNode(scene).getLights()).toEqual([sun, lamp]);
  lighting.finishRender(scene);
  expect(lighting.getNode(scene).getLights()).toEqual([sun, lamp]);
});

test('a fleet batch built after the frame (deferred or paced build) is lit by the sun', async () => {
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false; Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  const lighting = new GameLighting({ point: 4 }), world = new THREE.Scene(), sun = new THREE.DirectionalLight();
  sun.castShadow = true; world.add(sun, sun.target);
  const visuals = new ParkedCarVisuals(world), manifest = draftManifest('http://localhost/');
  manifest.vehicles = [{ object_id: 'a', model: 'fleet-sedan-0', color: 'red', x: 0, y: 0, z: 0, yaw: 0, width: 1.8, length: 4.5,
    height: 1.5, clear_front_m: 9, clear_rear_m: 9 }];
  await visuals.load(manifest);
  const batch = world.getObjectByName('parked/fleet-sedan-0/0') as THREE.InstancedMesh;
  expect(batch.receiveShadow).toBe(true);
  render(lighting, world, [sun]);
  const backend = renderer.backend as typeof renderer.backend & { createNodeBuilder(o: THREE.Object3D, r: THREE.WebGPURenderer): Builder };
  // Node and uniform ids differ per build; the program's structure is what the lights change.
  const program = (lightsNode: THREE.LightsNode) => {
    const builder = backend.createNodeBuilder(batch, renderer);
    builder.scene = world; builder.camera = new THREE.PerspectiveCamera(); builder.lightsNode = lightsNode;
    builder.build(); return builder.fragmentShader.replace(/\d+/g, '#');
  };
  const sunlit = program(lighting.createNode([sun])), unlit = program(lighting.createNode([]));
  expect(sunlit).not.toBe(unlit);
  // Built between frames from the world's own lights node, as deferred and paced builds are: lit by the sun. Unlit, the
  // paint came out pale under environment light alone (and without the sun's cascaded shadows in the game).
  expect(program(lighting.getNode(world))).toBe(sunlit);
});
