import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { modelViewPosition, uniform, vec3 } from 'three/tsl';
import fs from 'node:fs';
// @ts-expect-error r186 internal cache has no DefinitelyTyped declaration.
import NodeManager from 'three/src/renderers/common/nodes/NodeManager.js';
// @ts-expect-error r186 internal render object has no DefinitelyTyped declaration.
import RenderObject from 'three/src/renderers/common/RenderObject.js';
import { GameRenderer } from '../../src/render/shadowMaterials';
import { coveredBindings, observable, ownedUniforms, Refresh, staticRefresh, texturesChanged, type Draw } from '../../src/render/staticRefresh';
import { sceneryMaterial } from '../../src/render/sceneryMaterial';
import { wetSurface } from '../../src/render/weather/surfaces';

/* eslint-disable @typescript-eslint/no-explicit-any */
function fixture() {
  const renderer = new GameRenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  renderer.hasFeature = () => false;
  Object.assign(renderer.backend, { capabilities: { getUniformBufferLimit: () => 65536 } });
  const nodes = new NodeManager(renderer, renderer.backend), scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera();
  const lights = renderer.lighting.getNode(scene), context = { id: 1 };
  const draw = (material: THREE.Material, mesh = new THREE.Mesh(new THREE.BoxGeometry(), material)) => {
    const value = new RenderObject(nodes, {}, renderer, mesh, material, scene, camera, lights, context, null);
    nodes.getForRender(value);
    return value as Draw & { object: THREE.Mesh; getBindings(): any[] };
  };
  return { renderer, nodes, draw };
}
const covers = (value: Draw) => {
  const state = value.getNodeBuilderState();
  return observable(state, value.getMonitor().refreshUniforms) && coveredBindings(value, ownedUniforms(state));
};

test('r186 programs whose per-object inputs the observer sees are covered; callbacks, camera terms and free uniforms are not', () => {
  const { draw } = fixture();
  // A textured standard material: world matrix, normal matrix, material references, the map's uv transform.
  expect(covers(draw(new THREE.MeshStandardNodeMaterial({ map: new THREE.Texture(), roughness: .8 })))).toBe(true);
  expect(covers(draw(new THREE.MeshBasicNodeMaterial()))).toBe(true);
  // The block shells and distant city (sceneryMaterial): node colour and window graphs over plain references.
  expect(covers(draw(sceneryMaterial('buildings-lod-glazed')))).toBe(true);
  // Wet mineral surfaces: weather, height field and lamp field are render-group uniforms.
  expect(covers(draw(wetSurface(new THREE.MeshStandardNodeMaterial({ map: new THREE.Texture() }))))).toBe(true);
  // A per-object callback (lodFade coverage, shell roof mapping) can change while the object stands still.
  const callback = new THREE.MeshStandardNodeMaterial();
  callback.colorNode = vec3(1).mul(uniform(1).onObjectUpdate(() => Math.random()));
  expect(covers(draw(callback))).toBe(false);
  // A camera-relative per-object value (the object's view position).
  const view = new THREE.MeshStandardNodeMaterial();
  view.colorNode = vec3(modelViewPosition.z.mul(.001));
  expect(covers(draw(view))).toBe(false);
  // A per-object uniform no node owns: code sets its value at any time (groundField's night, before renderGroup).
  const free = new THREE.MeshStandardNodeMaterial(), night = uniform(0);
  free.emissiveNode = vec3(night);
  expect(covers(draw(free))).toBe(false);
});

test('a covered static draw refreshes once per program and pass, and in full for a frame after each change', () => {
  const { renderer, nodes, draw } = fixture();
  const own = nodes.needsRefresh;
  const refresh = staticRefresh({ _nodes: nodes, backend: renderer.backend, info: { frame: 0 } });
  const frame = nodes.getNodeFrame();
  const material = new THREE.MeshStandardNodeMaterial({ roughness: .5 });
  const a = draw(material), b = draw(material);
  const pass = () => { frame.renderId++; return [nodes.needsRefresh(a), nodes.needsRefresh(b)]; };
  expect(pass()).toEqual([Refresh.FULL, Refresh.FULL]);   // first sight of each draw
  expect(pass()).toEqual([Refresh.FULL, Refresh.FULL]);   // one settling frame (motion vectors)
  expect(pass()).toEqual([Refresh.SHARED, Refresh.NONE]); // the program's shared groups once, then nothing
  a.object.matrixWorld.makeTranslation(1, 0, 0);          // the object moves
  // Its full refresh writes the program's shared groups too, so the other draw has nothing left to do.
  expect(pass()).toEqual([Refresh.FULL, Refresh.NONE]);
  expect(pass()).toEqual([Refresh.FULL, Refresh.NONE]);   // settles for one frame
  expect(pass()).toEqual([Refresh.SHARED, Refresh.NONE]);
  material.roughness = .9;                                // a material uniform changes (no version bump)
  expect(pass()).toEqual([Refresh.FULL, Refresh.FULL]);
  pass();
  expect(pass()).toEqual([Refresh.SHARED, Refresh.NONE]);
  // A program the observer does not cover keeps r186's own answer (FULL for node materials).
  const callback = new THREE.MeshStandardNodeMaterial();
  callback.colorNode = vec3(uniform(1).onObjectUpdate(() => 1));
  const c = draw(callback);
  for (let i = 0; i < 3; i++) { frame.renderId++; expect(nodes.needsRefresh(c)).toBe(Refresh.FULL); }
  expect(refresh.stats.none).toBe(5);
  refresh.release();
  expect(nodes.needsRefresh).toBe(own);
});

test('a texture swap or upload behind a covered draw is seen per draw', () => {
  const { draw } = fixture();
  const map = new THREE.Texture(), value = draw(new THREE.MeshStandardNodeMaterial({ map }));
  for (const group of value.getBindings()) for (const binding of group.bindings) if (binding.isSampledTexture) binding.update();
  expect(texturesChanged(value)).toBe(false);
  map.needsUpdate = true;
  expect(texturesChanged(value)).toBe(true);
  for (const group of value.getBindings()) for (const binding of group.bindings) if (binding.isSampledTexture) binding.update();
  expect(texturesChanged(value)).toBe(false);
});

test('r186 contracts the static refresh relies on', () => {
  const read = (path: string) => fs.readFileSync(new URL(`../../node_modules/three/src/${path}`, import.meta.url), 'utf8');
  const observer = read('materials/nodes/manager/NodeMaterialObserver.js');
  // The early full refresh for node materials and velocity passes, then the classic per-render SHARED/NONE path.
  expect(observer).toMatch(/if \( this\.hasNode \|\| this\.hasAnimation \|\| this\.hasDynamicInstancing\( renderObject\.object \) \|\| this\.firstInitialization\( renderObject \) \|\| this\.needsVelocity\( nodeFrame\.renderer \) \)\s*return RenderObjectRefreshType\.FULL;/);
  expect(observer).toMatch(/if \( this\.renderId !== renderId \) \{\s*this\.renderId = renderId;/);
  expect(observer).toMatch(/const lightsData = this\.getLights\( renderObject\.lightsNode, renderId \);\s*if \( this\.equals\( renderObject, lightsData, renderId \) === false \)/);
  // NONE skips every update of the draw; SHARED updates the shared groups and the nodes.
  const renderer = read('renderers/common/Renderer.js');
  expect(renderer).toMatch(/const refreshType = this\._nodes\.needsRefresh\( renderObject \);\s*if \( refreshType === RenderObjectRefreshType\.FULL \) \{\s*this\._nodes\.updateBefore\( renderObject \);\s*this\._geometries\.updateForRender\( renderObject \);\s*this\._nodes\.updateForRender\( renderObject \);\s*this\._bindings\.updateForRender\( renderObject \);\s*\} else if \( refreshType === RenderObjectRefreshType\.SHARED \) \{\s*this\._nodes\.updateBefore\( renderObject \);\s*this\._nodes\.updateForRender\( renderObject \);\s*this\._bindings\.updateSharedForRender\( renderObject \);/);
  // Shared groups are one instance per program; per-object groups are cloned per draw.
  expect(read('renderers/common/nodes/NodeBuilderState.js')).toMatch(/if \( shared !== true \) \{[\s\S]*?instanceBinding\.clone\(\)[\s\S]*?\} else \{\s*bindings\.push\( instanceGroup \);/);
});
