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

window.runMaterialStream = async (urls, seconds, tier = 'low', moving = false) => {
  const renderer = new GameRenderer(); document.body.append(renderer.domElement);
  renderer.setSize(960, 540); await renderer.init(); renderer.shadowMap.enabled = true;
  retainPrograms(renderer._pipelines);
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(65, 960 / 540, .1, 6000);
  camera.position.set(180, 80, 330); camera.lookAt(100, 8, 100); camera.updateMatrixWorld();
  const env = new Environment(scene, renderer, tier); env.setTime(new Date('2026-07-01T12:00:00Z')); env.follow(camera.position); env.updateIBL();
  const post = new PostProcessing(renderer, scene, camera, tier, 'taa');
  const styler = new MaterialStyler(renderer, env, tier, false), shared = new SharedMaterials(), tiles = new VisualTiles();
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
  let passes = {}, transparentLists = {}, drawFrames = [];
  const drawObject = renderer.renderObject;
  renderer.renderObject = function(object, scene, camera, ...args) {
    const pass = scene.name.includes('[ Reflector ]') ? 'reflection'
      : scene.overrideMaterial?.isShadowPassMaterial ? 'shadow'
      : scene.overrideMaterial?.userData.normalPrepass ? 'pre' : object.isQuadMesh ? 'quad' : 'beauty';
    passes[pass] = (passes[pass] ?? 0) + 1;
    return drawObject.call(this, object, scene, camera, ...args);
  };
  const transparent = renderer._renderTransparents;
  renderer._renderTransparents = function(list, doubles, camera, scene, ...args) {
    const pass = scene.name.includes('[ Reflector ]') ? 'reflection' : 'beauty';
    transparentLists[pass] = (transparentLists[pass] ?? 0) + list.length;
    return transparent.call(this, list, doubles, camera, scene, ...args);
  };
  const live = [], rows = [], frames = [], costs = [], errors = [];
  renderer.backend.device.addEventListener('uncapturederror', e => errors.push(e.error.message));
  let running = true, last = performance.now(), motionFrame = 0;
  function frame() {
    if (!running) return;
    const now = performance.now(); frames.push(now - last); last = now;
    if (moving) { camera.position.x = 180 + Math.sin((motionFrame++ % 120) * Math.PI / 60) * 0.6; camera.lookAt(100, 8, 100); camera.updateMatrixWorld(); }
    passes = {}; transparentLists = {};
    const started = performance.now(); post.render(); costs.push(performance.now() - started);
    drawFrames.push({ ...renderer.info.render, passes, transparentLists });
    requestAnimationFrame(frame);
  }
  frame();
  const remove = model => { model.removeFromParent(); tiles.releasePreparedModel(model); styler.release(model); disposeModelResources(model, shared); };
  const sample = (step) => {
    const materials = new Set(), transparents = {}; let meshes = 0;
    scene.traverse(o => { if (o.isMesh) { meshes++; for (const m of Array.isArray(o.material) ? o.material : [o.material]) { materials.add(m); if (m.transparent) { const key = m.name || o.userData.material_key || 'unnamed'; transparents[key] = (transparents[key] ?? 0) + 1; } } } });
    rows.push({ step, at: performance.now(), models: live.length, meshes, materials: materials.size,
      transparents, memory: { ...renderer.info.memory }, drawFrames: drawFrames.splice(0),
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
    await new Promise(resolve => setTimeout(resolve, seconds * 1000 / 9));
    const wasMoving = moving; moving = false;
    camera.position.x = 180; camera.lookAt(100, 8, 100); camera.updateMatrixWorld();
    await new Promise(resolve => requestAnimationFrame(resolve));
    sample(i); moving = wasMoving; motionFrame = 0;
  }
  await window.captureMaterialStream?.();
  for (const model of live) remove(model); live.length = 0;
  await new Promise(resolve => requestAnimationFrame(resolve)); sample('released'); running = false;
  return { seconds: (performance.now() - started) / 1000, rows, errors };
};

// Exercise the exact async override path with rendering continuing across shader yields.
window.runMaterialPreparation = async () => {
  const renderer = new GameRenderer(); document.body.append(renderer.domElement); renderer.setSize(64, 64); await renderer.init();
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(65, 1, .1, 100);
  camera.position.z = 5;
  const post = new PostProcessing(renderer, scene, camera, 'low', 'taa');
  const errors = [], builders = [];
  renderer.backend.device.addEventListener('uncapturederror', e => errors.push(e.error.message));
  post.render();
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial({ side: THREE.DoubleSide }));
  const original = renderer.debug.onNodeBuilderCreated;
  renderer.debug.onNodeBuilderCreated = (builder, draw) => { original?.(builder, draw); if (draw.object === mesh) builders.push({ builder, draw }); };
  let running = true;
  const render = () => { if (running) { post.render(); requestAnimationFrame(render); } }; render();
  await post.prepare(mesh);
  const pre = builders.filter(({ draw }) => draw.material.name === 'normal-velocity-prepass');
  const snapshots = pre.map(({ builder }) => builder.renderer.getRenderTarget() === post.pre.renderTarget && builder.renderer.getMRT() === post.pre.getMRT());
  const before = builders.length;
  scene.add(mesh);
  for (let i = 0; i < 10; i++) await new Promise(resolve => requestAnimationFrame(resolve));
  running = false;
  await renderer.backend.device.queue.onSubmittedWorkDone();
  return { preBuilders: pre.length, snapshots, lateBuilders: builders.length - before, errors };
};
