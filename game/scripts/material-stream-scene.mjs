// Real immutable tile assets, production styling/passes, deterministic residency replay.
import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { GameRenderer } from '../src/render/shadowMaterials';
import { Environment } from '../src/render/environment';
import { PostProcessing } from '../src/render/post';
import { MaterialStyler } from '../src/render/materials';
import { tintDraft } from '../src/render/facadeKit';
import { SharedMaterials } from '../src/render/sharedMaterials';
import { LoadingKtx } from '../src/world/loadingKtx';
import { textureUploads } from '../src/render/textureUploads';
import { VisualTiles } from '../src/world/tiles';
import { disposeModelResources } from '../src/world/disposeModel';
import { retainPrograms } from '../src/render/programCache';

window.runMaterialStream = async (urls, seconds) => {
  const renderer = new GameRenderer(); document.body.append(renderer.domElement);
  renderer.setSize(960, 540); await renderer.init(); renderer.shadowMap.enabled = true;
  retainPrograms(renderer._pipelines);
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(65, 960 / 540, .1, 6000);
  camera.position.set(180, 80, 330); camera.lookAt(100, 8, 100); camera.updateMatrixWorld();
  const env = new Environment(scene, renderer, 'low'); env.setTime(new Date('2026-07-01T12:00:00Z')); env.follow(camera.position); env.updateIBL();
  const post = new PostProcessing(renderer, scene, camera, 'low', 'taa');
  const styler = new MaterialStyler(renderer, env, 'low', false), shared = new SharedMaterials(), tiles = new VisualTiles();
  const ktx = new LoadingKtx().setTranscoderPath('/basis/').detectSupport(renderer);
  const loader = new GLTFLoader().setKTX2Loader(ktx).setMeshoptDecoder(MeshoptDecoder).register(() => textureUploads(renderer).plugin());
  let builds = 0, created = 0, bindingUpdates = 0;
  const draws = new Set();
  const original = renderer.debug.onNodeBuilderCreated;
  renderer.debug.onNodeBuilderCreated = (...args) => { builds++; original?.(...args); };
  const create = renderer._objects.createRenderObject;
  renderer._objects.createRenderObject = function(...args) { created++; const draw = create.apply(this, args); draws.add(draw); const dispose = draw.onDispose; draw.onDispose = () => { draws.delete(draw); dispose(); }; return draw; };
  const update = renderer._bindings.updateForRender;
  renderer._bindings.updateForRender = function(...args) { bindingUpdates++; return update.apply(this, args); };
  const live = [], rows = [], frames = [], costs = [], errors = [];
  renderer.backend.device.addEventListener('uncapturederror', e => errors.push(e.error.message));
  let running = true, last = performance.now();
  function frame() {
    if (!running) return;
    const now = performance.now(); frames.push(now - last); last = now;
    const started = performance.now(); post.render(); costs.push(performance.now() - started);
    requestAnimationFrame(frame);
  }
  frame();
  const remove = model => { model.removeFromParent(); tiles.releasePreparedModel(model); styler.release(model); disposeModelResources(model, shared); };
  const sample = (step) => {
    const materials = new Set(); let meshes = 0;
    scene.traverse(o => { if (o.isMesh) { meshes++; for (const m of Array.isArray(o.material) ? o.material : [o.material]) materials.add(m); } });
    rows.push({ step, at: performance.now(), models: live.length, meshes, materials: materials.size,
      renderObjects: draws.size, pipelines: renderer._pipelines.caches.size,
      builds, created, bindingUpdates, frames: frames.splice(0), costs: costs.splice(0) });
  };
  const started = performance.now();
  for (let i = 0; i < 9; i++) {
    const model = (await loader.loadAsync(urls[i % urls.length])).scene;
    await styler.applyAsync(model); tintDraft(model, renderer, shared); await tiles.prepareModelAsync(model);
    scene.add(model); live.push(model);
    // Match retirement: finish a frame before releasing the oldest tile.
    await new Promise(resolve => requestAnimationFrame(resolve));
    if (live.length > 2) remove(live.shift());
    await new Promise(resolve => setTimeout(resolve, seconds * 1000 / 9)); sample(i);
  }
  await window.captureMaterialStream?.();
  for (const model of live) remove(model); live.length = 0;
  await new Promise(resolve => requestAnimationFrame(resolve)); sample('released'); running = false;
  return { seconds: (performance.now() - started) / 1000, rows, errors };
};
