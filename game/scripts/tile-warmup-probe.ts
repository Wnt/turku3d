// Capture-only Vite entry. No production entry imports this fixture.
import * as THREE from 'three/webgpu';
import { GameRenderer } from '../src/render/shadowMaterials';
import { PostProcessing } from '../src/render/post';
import { Environment } from '../src/render/environment';
import { VisualTiles } from '../src/world/tiles';
import { MaterialStyler } from '../src/render/materials';

const renderer = new GameRenderer({ antialias: false });
renderer.setSize(640, 480); renderer.shadowMap.enabled = true;
document.body.append(renderer.domElement);
await renderer.init();
const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(60, 640 / 480, 0.1, 6000);
camera.position.set(0, 5, 12); camera.lookAt(0, 1, 0); camera.updateMatrixWorld();
const environment = new Environment(scene, renderer, 'medium');
environment.setTime(new Date('2026-10-15T11:00:00Z')); environment.follow(new THREE.Vector3()); environment.updateIBL();
const floor = new THREE.Mesh(new THREE.BoxGeometry(40, 0.1, 40), new THREE.MeshStandardMaterial());
floor.name = 'resident-floor'; floor.receiveShadow = floor.castShadow = true; scene.add(floor);
const post = new PostProcessing(renderer, scene, camera, 'medium');
const styler = new MaterialStyler(renderer, environment, 'medium', true), tiles = new VisualTiles();
for (let i = 0; i < 3; i++) { post.render(); await new Promise(requestAnimationFrame); }
const builds: object[] = [], stages: object[] = [];
let phase = 'prepare';
renderer.debug.onNodeBuilderCreated = (builder, object) => {
  builds.push({ phase, mesh: object.object.name, receiveShadow: object.object.receiveShadow,
    key: object.initialCacheKey, currentKey: object.getCacheKey(), materialKey: object.getMaterialCacheKey(),
    dynamicKey: object.getDynamicCacheKey(), context: object.context.id, camera: object.camera.uuid,
    callDepth: renderer._callDepth, contextKeys: Object.entries(renderer._renderContexts._renderContexts).filter(([, value]: any) => value.id === object.context.id).map(([key]) => key) });
  const flow = builder.flowNode.bind(builder);
  builder.flowNode = (...args) => {
    stages.push({ phase, mesh: object.object.name, pass: builder.renderer.getMRT()?.uuid ?? null,
      target: builder.renderer.getRenderTarget()?.texture.uuid ?? null });
    return flow(...args);
  };
};
const model = new THREE.Group();
const source = new THREE.MeshStandardMaterial({ color: 0x999999 }); source.userData.material_key = 'asphalt';
const mesh = new THREE.Mesh(new THREE.BoxGeometry(3, 3, 3), source);
mesh.name = 'streamed-asphalt'; mesh.userData.layer = 'buildings'; model.add(mesh);
await styler.apply(model);
// Match draftWorld's final preparation order, using the new lifecycle when present.
if ('prepareModel' in tiles) tiles.prepareModel(model);
const start = performance.now();
await styler.prepare(model, scene, camera); await post.prepare(model);
const preparedMs = performance.now() - start;
const warmedReceiveShadow = mesh.receiveShadow;
phase = 'publish';
tiles.trackModel(model, 'synthetic'); scene.add(model);
const beforeRender = performance.now(); post.render();
const firstDrawMs = performance.now() - beforeRender;
phase = 'steady'; post.render();
(window as any).__warmupResult = { preparedMs, firstDrawMs, warmedReceiveShadow, publishedReceiveShadow: mesh.receiveShadow,
  builds, stages, publicationAoBuilds: builds.filter((row: any) => row.phase === 'publish' && row.mesh === 'AO').length, publicationBuilds: builds.filter((row: any) => row.phase === 'publish' && row.mesh === mesh.name).length };
