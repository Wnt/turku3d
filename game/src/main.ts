// Turku3D game skeleton entry. Modes:
//   play (default): third-person player + car, physics worker, collision streaming, debug HUD
//   QA (?cam=E,N,H,yaw,pitch,fov[&roll=R]&time=ISO[&pass=id]): the qa/README.md §2 render contract; exact camera
//   (vertical fov), no HUD/player/physics; sets window.__qaReady/__qaError/__qaPose/__qaInfo/__qaProject[/__qaIdTable]
import { loading } from './core/loading';
import { LoadingScreen } from './ui/loadingScreen';
import * as THREE from 'three/webgpu';
import { parseParams } from './core/params';
import { enToEngine, headingToYaw } from './core/origin';
import { createRenderer, defaultBackend } from './render/renderer';
import { DayClock } from './render/dayClock';
import { dayCycle } from './core/dayCycle';
import { Environment } from './render/environment';
import { PostProcessing } from './render/post';
import { PerformanceMonitor, type PerfSnapshot } from './render/performance';
import { AdaptiveResolution } from './render/adaptiveResolution';
import { renderPixelRatio } from './render/quality';
import { VegetationLod } from './world/vegetationLod';
import { MaterialStyler } from './render/materials';
import { VisualTiles } from './world/tiles';
import { findInitialGround } from './world/startGround';
import { draftManifest } from './world/manifest';
import { CollisionStreamer, STARTUP_COLLISION_RADIUS } from './world/collisionStreaming';
import { CoarseTiles } from './world/coarseTiles';
import { PhysicsClient } from './physics/client';
import { IdPass } from './render/idPass';
import { Telemetry, httpTransport, telemetryEnabled } from './telemetry';
import { BuildBadge, type BuildStatus } from './ui/buildBadge';
import { startPlay, type PlayWorld } from './play';
import { worldLoad } from './world/loadProgress';

declare global {
  interface Window {
    __qaReady?: boolean;
    __qaError?: string;
    __qaPose?: { E: number; N: number; H: number; yaw: number; pitch: number; roll: number; fov: number };
    __qaInfo?: Record<string, unknown>;
    __qaIdTable?: Record<string, unknown>;
    __qaProject?: (E: number, N: number, H: number) => [number, number] | null;
    __qaMove?: (pose: { E: number; N: number; H: number; yaw: number; pitch: number; fov: number }) => void;
    __game?: Record<string, unknown>;
    __perf?: PerfSnapshot;
    __render?: { camera: THREE.PerspectiveCamera; environment: Environment; renderer: THREE.WebGPURenderer;
      perf: PerformanceMonitor; setTime: (iso: string) => void; beforeFrame?: () => boolean; afterFrame?: () => void };
  }
}

let params: ReturnType<typeof parseParams>;
try {
  params = parseParams(location.search);
} catch (e) {
  window.__qaError = String((e as Error).message ?? e); // bad cam/time/pass: the QA driver stops waiting
  throw e;
}
const loadingScreen = new LoadingScreen(!!params.qa);
const hudEl = document.getElementById('hud')!;
const errors: string[] = [];
const fail = (e: unknown) => {
  const msg = String((e as Error)?.stack ?? e);
  worldLoad.update({ phase: 'error', errors: [msg] });
  errors.push(msg);
  console.error(msg);
  loadingScreen.fail(msg);
  hudEl.textContent = `ERROR: ${msg}`;
  window.__qaError = msg;
};
addEventListener('error', (e) => { errors.push(String(e.message)); loadingScreen.fail(String(e.message)); });
addEventListener('unhandledrejection', (e) => { errors.push(String(e.reason)); loadingScreen.fail(String(e.reason)); });

const idPass = params.qa && params.pass === 'id' ? new IdPass() : null;
const { renderer, backend } = await loading.track('GPU renderer initialization', createRenderer(await defaultBackend(params.backend, params.backendGiven), document.body,
  { qa: !!params.qa, idPass: !!idPass }));
const perf = new PerformanceMonitor(renderer, backend);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.1, 6000);
camera.rotation.order = 'YXZ';
const env = new Environment(scene, renderer, params.quality);
env.setTime(params.qa || params.timeGiven ? params.time : dayCycle(Date.now()).lightTime);
if (!idPass) env.updateIBL();
const post = idPass ? null : new PostProcessing(renderer, scene, camera, params.quality, params.aa);
Object.defineProperty(window, '__perf', { get: () => perf.snapshot() });
/** QA stays fixed; play follows the shared UTC cycle unless explicitly set through the developer tools. */
const dayClock = new DayClock(env, params.qa || params.timeGiven ? params.time : null, !params.qa);
window.__render = { camera, environment: env, renderer, perf, setTime: (iso: string) => {
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) throw new Error('invalid render time');
  dayClock.set(date);
} };
const vegetation = idPass ? null : new VegetationLod(params.quality);
const tiles = new VisualTiles(!!idPass);
const materials = idPass ? null : new MaterialStyler(renderer, env, params.quality, !!params.qa);
tiles.events.addEventListener('dispose-model', ({ scene: model }: { scene: THREE.Object3D }) => { vegetation?.remove(model); materials?.release(model); });
let graphicsReady = Promise.resolve();
const prepareGraphics = (model: THREE.Object3D, signal?: AbortSignal) => {
  // Startup warms the complete scene with real draws before Ready. Per-model async compilation
  // repeats that work and yields once per material; reserve paced compilation for streaming during play.
  if (!params.qa && worldLoad.snapshot().phase !== 'ready') return Promise.resolve();
  const prepared = graphicsReady.then(async () => {
    if (signal?.aborted) return;
    await materials?.prepare(model, scene, camera, signal);
    if (!signal?.aborted) await post?.prepare(model);
  });
  graphicsReady = prepared.catch(() => {});
  return prepared;
};
const resolution = new AdaptiveResolution();
const renderFrame = () => {
  if (!params.qa && worldLoad.snapshot().phase === 'ready' && document.visibilityState === 'visible') {
    const scale = resolution.update(performance.now());
    const ratio = renderPixelRatio(innerWidth, innerHeight, devicePixelRatio, false) * scale;
    if (Math.abs(renderer.getPixelRatio() - ratio) > 0.001) renderer.setPixelRatio(ratio);
  }
  dayClock.update();
  vegetation?.update(camera);
  if (post) post.render(); else renderer.render(scene, camera);
};

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  if (params.qa) applyQaCamera();
  camera.updateProjectionMatrix();
  renderer.setPixelRatio(renderPixelRatio(innerWidth, innerHeight, devicePixelRatio, !!params.qa));
  renderer.setSize(innerWidth, innerHeight);
  env.resize();
});

// Test clock: rendered frames and simulated seconds (60 Hz physics steps in play mode, frame time clamped like the
// play loop's dt in QA mode). Browser checks budget their waits in these units, so a starved host makes a run
// slower instead of failing it (tests/e2e/waits.ts).
const clock = { frames: 0, simSeconds: 0 };
window.__game = { dayClock, backend, params, errors, tiles, clock, resolution, three: THREE.REVISION, loadProgress: () => worldLoad.snapshot() };

if (params.qa) runQa().catch(fail);
else runPlay().catch(fail);

// ------------------------------------------------------------------------------------------- QA mode
function applyQaCamera() {
  // qa/README.md §2 / qa/qalib/camera.py: rotation order YXZ = (pitch, -yaw, -roll), fov is VERTICAL
  const q = params.qa!;
  const p = enToEngine(q.E, q.N, q.H);
  const d2r = Math.PI / 180;
  camera.position.set(p.x, p.y, p.z);
  camera.rotation.set(q.pitchDeg * d2r, headingToYaw(q.yawDeg), -q.rollDeg * d2r, 'YXZ');
  camera.fov = q.fovDeg;
  camera.aspect = innerWidth / innerHeight;
  camera.near = 0.1;
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
}

async function draftWorld() {
  const physics = new PhysicsClient();
  physics.onError = e => fail(`physics: ${e}`);
  const rapier = await physics.ready;
  const manifest = draftManifest(location.href);
  const streamer = new CollisionStreamer(manifest, physics, params.collisionRadius);
  const coarse = await CoarseTiles.start({ scene, renderer, manifest,
    unloadCollision: id => physics.unloadTile(id), refreshCollision: ids => streamer.refresh(ids),
    checkCollision: buffer => physics.checkTile(buffer),
    retainCollision: (id, entry, buffer) => streamer.retain(id, entry, buffer),
    trackModel: (m, id) => tiles.trackModel(m, id), untrackModel: (m, id) => tiles.untrackModel(m, id),
    style: m => materials?.applyAsync(m), unstyle: m => { vegetation?.remove(m); tiles.releasePreparedModel(m); materials?.release(m); },
    styled: async m => { await tiles.prepareModelAsync(m); await vegetation?.addAsync(m); await prepareGraphics(m); } });
  if (!coarse) throw new Error('World generation service is unavailable');
  return { physics, rapier, manifest, streamer, coarse };
}

async function runQa() {
  const q = params.qa!;
  document.body.classList.add('qa');
  applyQaCamera();
  const { coarse } = await draftWorld();
  window.__qaPose = { E: q.E, N: q.N, H: q.H, yaw: q.yawDeg, pitch: q.pitchDeg, roll: q.rollDeg, fov: q.fovDeg };
  window.__qaProject = (E: number, N: number, H: number) => {
    const p = enToEngine(E, N, H);
    const v = new THREE.Vector3(p.x, p.y, p.z).applyMatrix4(camera.matrixWorldInverse);
    if (v.z >= -1e-6) return null; // behind the camera
    v.applyMatrix4(camera.projectionMatrix);
    return [((v.x + 1) / 2) * innerWidth, ((1 - v.y) / 2) * innerHeight];
  };
  const loadedTiles: string[] = [];
  tiles.events.addEventListener('load-model', ({ scene: model, tile }: { scene: THREE.Object3D; tile: { extras?: { id?: string } } }) => {
    const id = tile?.extras?.id ?? null;
    if (id) loadedTiles.push(id);
    idPass?.apply(model, id);
  });
  if (idPass) {
    IdPass.prepare(renderer, scene, [env.sky]);
    window.__qaIdTable = idPass.table;
  }
  window.__qaInfo = { renderer: `three.js r${THREE.REVISION} WebGPURenderer`, backend, pass: params.pass,
    time: params.timeGiven ? params.time.toISOString() : null, three: THREE.REVISION, tiles: loadedTiles, world: 'draft-service',
    quality: params.quality, effectiveTime: params.time.toISOString(), sseUnits: 'drawing-buffer-pixels' };
  env.follow(camera.position.clone().add(camera.getWorldDirection(new THREE.Vector3()).multiplyScalar(40)));
  let stable = 0;
  let t0 = performance.now();
  window.__qaMove = pose => {
    Object.assign(q, { E: pose.E, N: pose.N, H: pose.H, yawDeg: pose.yaw,
      pitchDeg: pose.pitch, fovDeg: pose.fov });
    applyQaCamera();
    window.__qaPose = { ...pose, roll: q.rollDeg };
    env.follow(camera.position.clone().add(camera.getWorldDirection(new THREE.Vector3()).multiplyScalar(40)));
    window.__qaReady = false;
    window.__qaError = undefined;
    stable = 0;
    t0 = performance.now();
    perf.reset();
  };
  let last = t0;
  renderer.setAnimationLoop(() => {
    if (window.__render?.beforeFrame?.() === false) return;
    const now = performance.now();
    clock.frames++;
    clock.simSeconds += Math.min(0.1, (now - last) / 1000); // the play loop's dt clamp
    last = now;
    perf.begin();
    coarse.update(camera.position.x, camera.position.z);
    renderFrame();
    perf.end();
    window.__render?.afterFrame?.();
    if (window.__qaReady || window.__qaError) return;
    const progress = coarse.progressAt(camera.position.x, camera.position.z);
    if (progress.errors.length) { window.__qaError = progress.errors.join('; '); return; }
    if (materials?.ground?.errors.length) { window.__qaError = materials.ground.errors[0]; return; }
    stable = progress.total > 0 && progress.loaded === progress.total && !materials?.ground?.pending ? stable + 1 : 0;
    // Allow temporal colour history to converge after textures finish uploading (QA contract: >= 2 frames).
    if (stable >= 32) {
      window.__qaReady = true;
      window.__game!.qa = { loadMs: Math.round(performance.now() - t0), visTris: tiles.residentTris, models: tiles.residentModels, sunElevation: env.sunElevation, sunAzimuth: env.sunAzimuth };
    }
    if (clock.simSeconds > 120) window.__qaError = 'timeout: tiles did not settle in 120 s of frame time';
  });
}

// ------------------------------------------------------------------------------------------- play mode
async function runPlay() {
  // Play telemetry (telemetry/README.md): local origins only, never on the published Pages site.
  const telemetry = telemetryEnabled(location)
    ? new Telemetry(httpTransport(`${location.origin}/telemetry`)) : null;
  if (telemetry) {
    tiles.events.addEventListener('load-model', ({ scene: model, tile }: { scene: THREE.Object3D; tile: { extras?: { id?: string } } }) =>
      telemetry.addModel(model, tile?.extras?.id));
    tiles.events.addEventListener('dispose-model', ({ scene: model }: { scene: THREE.Object3D }) => telemetry.removeModel(model));
    addEventListener('pagehide', () => telemetry.flush(true));
    addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') telemetry.flush(true); });
  }
  const { physics, rapier, manifest, streamer, coarse } = await draftWorld();
  // spawn: wait for the collision tile under the spawn point, then drop onto the ground
  const requested = enToEngine(params.spawn.E, params.spawn.N, 0);
  // Limit demand before the first update: otherwise the full 3x3 window is already in flight.
  streamer.radius = Math.min(params.collisionRadius, STARTUP_COLLISION_RADIUS);
  coarse.preload([requested], streamer.radius);
  const tWait = performance.now();
  let placed: Awaited<ReturnType<typeof findInitialGround>> = null;
  while (!placed) {
    if (performance.now() - tWait > 600_000) throw new Error(`No supported street start after 10 minutes: ${coarse.status}`);
    coarse.update(requested.x, requested.z);
    streamer.update(requested.x, requested.z);
    const progress = coarse.progressAt(requested.x, requested.z);
    worldLoad.update({ visual: { loaded: progress.loaded, total: progress.total } });
    loadingScreen.update(false, coarse.status);
    if (streamer.supports(requested.x, requested.z)) {
      physics.step(1 / 60, { vx: 0, vz: 0, jump: false }, null);
      placed = await findInitialGround(requested, coarse.roadGraphs(),
        (x, z) => physics.raycast({ x, y: 500, z }, { x: 0, y: -1, z: 0 }, 1000),
        (x, z, radius) => streamer.supports(x, z, radius));
    }
    if (!placed) await new Promise(r => setTimeout(r, 100));
  }
  const { spawn, car: parking } = placed;
  const groundAt = async (x: number, z: number) => (await physics.raycast({ x, y: 500, z }, { x: 0, y: -1, z: 0 }, 1000))?.point.y ?? spawn.y;
  physics.spawnCharacter({ x: spawn.x, y: spawn.y + 0.05, z: spawn.z });
  const yaw0 = headingToYaw(params.spawn.headingDeg);
  const carPos = parking, carGround = parking.y, carYaw = parking.yaw;
  physics.spawnVehicle({ x: carPos.x, y: carGround + 0.75, z: carPos.z }, carYaw);
  const world: PlayWorld = { params, backend, renderer, scene, camera, env, perf, tiles, materials, physics, rapier, manifest,
    streamer, coarse, telemetry, clock, errors, fail, hudEl, renderFrame, prepareGraphics, groundAt,
    loadingScreen, syncClock: time => dayClock.synchronize(time), clockTime: () => dayClock.time, setClock: date => dayClock.restore(date),
    spawn: { ...spawn, yaw: yaw0 }, car: { x: carPos.x, y: carGround, z: carPos.z, yaw: carYaw } };
  // Localhost game (Vite dev server, tools/dev/localhost-game): a landing that touches only the play session swaps it in
  // place; the world above keeps the player, the vehicles and the loaded tiles. The build badge names main's commit
  // and its verification verdict. Production builds drop this (import.meta.hot is undefined there).
  const badge = import.meta.hot ? new BuildBadge(document.body) : null;
  let session = await startPlay(world, null);
  worldLoad.update({ phase: 'ready' });
  if (import.meta.hot && badge) {
    const onStatus = (status: BuildStatus) => badge.set(status);
    import.meta.hot.on('turku3d:status', onStatus);
    void fetch('/__localhost/status').then(r => r.json()).then(onStatus).catch(() => {});
    badge.applied('loaded');
    let swapping = Promise.resolve();
    // Vite finds the accepted module by this literal call; an alias of import.meta.hot would not register it.
    import.meta.hot.accept('./play', next => {
      if (!next) return;
      swapping = swapping.then(async () => {
        session = await (next as unknown as { startPlay: typeof startPlay }).startPlay(world, session.dispose());
        badge.applied('hot-swap');
      }).catch(fail);
    });
  }
}
