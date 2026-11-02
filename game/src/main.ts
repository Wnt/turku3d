import { collisionPreview } from './world/collisionPreview';
// Turku3D game skeleton entry. Modes:
//   play (default): third-person player + car, physics worker, collision streaming, debug HUD
//   QA (?cam=E,N,H,yaw,pitch,fov[&roll=R]&time=ISO[&pass=id]): the qa/README.md §2 render contract; exact camera
//   (vertical fov), no HUD/player/physics; sets window.__qaReady/__qaError/__qaPose/__qaInfo/__qaProject[/__qaIdTable]
//   bench (?bench=camera&id=<street camera>[&at=HH:MM][&quality=]): QA with a street camera's pose at the play-mode
//   pixel ratio; after settling, warms and measures fixed frames and sets window.__bench (qa/bench.ts)
import { loading } from './core/loading';
import { LoadingScreen } from './ui/loadingScreen';
import * as THREE from 'three/webgpu';
import { parseParams } from './core/params';
import { enToEngine, headingToYaw } from './core/origin';
import { createRenderer, defaultBackend } from './render/renderer';
import { DayClock, type ClockMode } from './render/dayClock';
import { Environment } from './render/environment';
import { riverClock } from './render/riverWaves';
import { PostProcessing } from './render/post';
import { StreetLights } from './render/lights';
import { PerformanceMonitor, type PerfSnapshot } from './render/performance';
import { AdaptiveResolution } from './render/adaptiveResolution';
import { renderPixelRatio } from './render/quality';
import { VegetationLod } from './world/vegetationLod';
import { loadNatureLibrary } from './world/nature/library';
import { PropLod } from './world/propLod';
import { MaterialStyler } from './render/materials';
import { VisualTiles } from './world/tiles';
import { findInitialGround } from './world/startGround';
import { draftManifest } from './world/manifest';
import { CollisionStreamer, STARTUP_COLLISION_RADIUS } from './world/collisionStreaming';
import { CoarseTiles, type CoarseHost } from './world/coarseTiles';
import { readCol } from './physics/colTile';
import { PhysicsClient } from './physics/client';
import { IdPass } from './render/idPass';
import { Telemetry, httpTransport, telemetryEnabled } from './telemetry';
import { BuildBadge, type BuildStatus } from './ui/buildBadge';
import { startPlay, type PlayWorld } from './play';
import { worldLoad } from './world/loadProgress';
import { retireAfterFrame, frameSubmitted } from './render/retirement';
import { CameraBench, benchPose } from './qa/bench';

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
    __bench?: Record<string, unknown>;
    __dev?: { setPose: NonNullable<Window['__qaMove']>; setHour: (hour: number) => void;
      pause: (paused: boolean) => void; bench?: (id: string, at: string) => void };
    __render?: { camera: THREE.PerspectiveCamera; environment: Environment; renderer: THREE.WebGPURenderer;
      perf: PerformanceMonitor; setTime: (iso: string) => void; setHour: (hour: number) => void;
      beforeFrame?: () => boolean; afterFrame?: () => void };
  }
}

let params: ReturnType<typeof parseParams>;
try {
  params = parseParams(location.search, navigator);
} catch (e) {
  window.__qaError = String((e as Error).message ?? e); // bad cam/time/pass: the QA driver stops waiting
  throw e;
}
const loadingScreen = new LoadingScreen(!!params.qa);
/** Bench probe: requests in flight with their start times, so a slow download keeps the scene unsettled. */
const benchFetches = new Map<number, number>();
if (params.bench) {
  const original = globalThis.fetch.bind(globalThis);
  let next = 0;
  globalThis.fetch = (...args: Parameters<typeof fetch>) => {
    const id = next++;
    benchFetches.set(id, performance.now());
    return original(...args).finally(() => benchFetches.delete(id));
  };
}
/** QA renders 1:1 with the viewport; the bench probe measures at the play-mode pixel ratio (adaptive scale 1). */
const qaCanvas = !!params.qa && !params.bench;
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
  { qa: qaCanvas, idPass: !!idPass, mobile: params.mobile, quality: params.quality }));
const perf = new PerformanceMonitor(renderer, backend);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.1, 6000);
camera.rotation.order = 'YXZ';
const env = new Environment(scene, renderer, params.quality);
/** QA and ?time= views stay fixed (local only); play follows the shared UTC cycle, which the server aligns. */
const clockMode: ClockMode = params.hour !== null ? { kind: 'hour', hour: params.hour }
  : params.qa || params.timeGiven ? { kind: 'date', date: params.time } : { kind: 'cycle', periodMs: params.cycleMinutes * 60_000 };
const dayClock = new DayClock(env, clockMode, !params.qa);
if (!idPass) env.updateIBL();
const lights = idPass ? null : new StreetLights(scene, params.quality, params.lights, env.lamps.point);
const post = idPass ? null : new PostProcessing(renderer, scene, camera, params.quality, params.aa, lights?.sprites);
Object.defineProperty(window, '__perf', { get: () => perf.snapshot() });
window.__render = { camera, environment: env, renderer, perf, setTime: (iso: string) => {
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) throw new Error('invalid render time');
  dayClock.set(date);
}, setHour: (hour: number) => {
  if (!Number.isFinite(hour)) throw new Error('invalid render hour');
  dayClock.setHour(hour);
} };
let natureLibrary = idPass ? null : loadNatureLibrary(renderer, env, !!params.qa && !params.motion);
let vegetation = natureLibrary ? new VegetationLod(params.quality, natureLibrary, scene) : null;
const furniture = idPass ? null : new PropLod();
const tiles = new VisualTiles(!!idPass, release => retireAfterFrame(renderer, release));
const materials = idPass ? null : new MaterialStyler(renderer, env, params.quality, !!params.qa && !params.motion);
tiles.events.addEventListener('dispose-model', ({ scene: model }: { scene: THREE.Object3D }) => {
  furniture?.remove(model);
  retireAfterFrame(renderer, () => { vegetation?.remove(model); materials?.release(model); });
});
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
const resolution = new AdaptiveResolution(params.mobile ? 30 : 60);
/** Set by the bench probe (runQa): true once its scene has settled. */
let benchSettled = () => false;
let distantCity: CoarseTiles['screenshotScenery'] | null = null;
const renderFrame = () => {
  if (!params.qa && worldLoad.snapshot().phase === 'ready' && document.visibilityState === 'visible') {
    const scale = resolution.update(performance.now(), params.mobile ? perf.frameCostMs : undefined);
    const ratio = renderPixelRatio(innerWidth, innerHeight, devicePixelRatio, false, params.mobile) * scale;
    if (Math.abs(renderer.getPixelRatio() - ratio) > 0.001) renderer.setPixelRatio(ratio);
  }
  dayClock.update();
  distantCity?.follow(camera);
  // QA fixes weather independently of the HUD; players share UTC-driven cloud drift.
  const sharedSeconds = dayClock.sharedNow() / 1000;
  env.atmosphere.setTime(params.qa ? 0 : sharedSeconds);
  riverClock.seconds = params.qa ? (params.motion ? clock.simSeconds : 0) : sharedSeconds;
  // Warmup renders every shadow pipeline whatever the hour; the cascades freeze at night only once play starts
  // (and in the bench probe once its scene has settled, so its night frame costs what play's does).
  env.updateShadows((!params.qa && worldLoad.snapshot().phase === 'ready') || benchSettled());
  post?.grade(env.sample);
  if (vegetation) vegetation.screenshotDistance = env.screenshotDistance;
  vegetation?.update(camera);
  furniture?.update(camera);
  lights?.update(camera, renderer, { time: dayClock.time, sunElevation: env.sunElevation });
  if (post) post.render(); else renderer.render(scene, camera);
  frameSubmitted(renderer);
};

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  if (params.qa) applyQaCamera();
  camera.updateProjectionMatrix();
  renderer.setPixelRatio(renderPixelRatio(innerWidth, innerHeight, devicePixelRatio, qaCanvas, params.mobile) * (params.qa ? 1 : resolution.scale));
  renderer.setSize(innerWidth, innerHeight);
  env.resize();
});

// Test clock: rendered frames and simulated seconds (60 Hz physics steps in play mode, frame time clamped like the
// play loop's dt in QA mode). Browser checks budget their waits in these units, so a starved host makes a run
// slower instead of failing it (tests/e2e/waits.ts).
const clock = { frames: 0, simSeconds: 0 };
window.__game = { dayClock, backend, params, errors, tiles, clock, resolution, three: THREE.REVISION, loadProgress: () => worldLoad.snapshot(),
  lights, vegetation };

// Rebuild vegetation in the loaded world when its renderer changes; keep the camera,
// streamed tiles and physics worker alive throughout a visual iteration.
let vegetationReplacement = Promise.resolve();
window.__game.replaceVegetation = (Renderer: typeof VegetationLod, replacement = natureLibrary) => {
  const install = async () => {
    if (!replacement || !vegetation) return;
    natureLibrary = replacement;
    const models = vegetation.loadedModels();
    vegetation.dispose();
    const next = new Renderer(params.quality, natureLibrary, scene);
    vegetation = next;
    window.__game!.vegetation = next;
    for (const model of models) await next.addAsync(model);
  };
  vegetationReplacement = vegetationReplacement.then(install, install);
  return vegetationReplacement;
};

// QA has no play session to replace; actor edits are applied through its runtime inspection hooks.
if (import.meta.hot && params.qa) import.meta.hot.accept('./play', () => {});
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
  const coarse = await draftVisuals(manifest, {
    unloadCollision: id => physics.unloadTile(id), refreshCollision: ids => streamer.refresh(ids),
    checkCollision: buffer => physics.checkTile(buffer),
    retainCollision: (id, entry, buffer) => streamer.retain(id, entry, buffer),
  });
  return { physics, rapier, manifest, streamer, coarse };
}

async function draftVisuals(manifest: CoarseHost['manifest'], collision: Pick<CoarseHost,
  'unloadCollision' | 'refreshCollision' | 'checkCollision' | 'retainCollision'>) {
  const coarse = await CoarseTiles.start({ scene, renderer, manifest,
    detailRadius: params.mobile ? 80 : undefined,
    previewCollision: collisionPreview,
    ...collision,
    trackModel: (m, id) => tiles.trackModel(m, id), untrackModel: (m, id) => tiles.untrackModel(m, id),
    retire: release => retireAfterFrame(renderer, release),
    style: m => materials?.applyAsync(m), unstyle: m => { vegetation?.remove(m); furniture?.remove(m); tiles.releasePreparedModel(m); materials?.release(m); },
    styled: async m => { await tiles.prepareModelAsync(m); await vegetation?.addAsync(m); furniture?.add(m); await prepareGraphics(m); } });
  if (!coarse) throw new Error('World generation service is unavailable');
  lights?.attach(coarse);
  if (!idPass) {
    distantCity = coarse.screenshotScenery;
    distantCity.setRadius(params.quality === 'low' || params.quality === 'mobile' ? 2500 : 4000);
    distantCity.follow(camera);
    // Keep the sky behind the far city; atmospheric haze still comes from the timecycle.
    env.setCityDistance(camera, distantCity.radius);
    window.__game!.scenery = distantCity;
  }
  return coarse;
}

async function runQa() {
  const q = params.qa!;
  document.body.classList.add('qa');
  applyQaCamera();
  // Fixed cameras need the same validated world geometry, but no simulation worker or Rapier startup.
  const coarse = await draftVisuals(draftManifest(location.href), {
    unloadCollision: () => {}, refreshCollision: () => {},
    checkCollision: async buffer => {
      const col = await readCol(buffer);
      if (!col.indices.length && !col.prims.length) throw new Error(`${col.id}: no collision geometry`);
    },
  });
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
  const bench = params.bench ? new CameraBench({
    backend: renderer.backend,
    pipelines: () => (renderer as unknown as { _pipelines?: { caches?: Map<unknown, unknown> } })._pipelines?.caches?.size ?? null,
    passes: () => post?.passTargets ?? {},
    perf,
    pause: paused => { window.__render!.beforeFrame = paused ? () => false : undefined; },
    now: () => performance.now(),
    busy: () => {
      const scenery = distantCity?.progress;
      // Derivative generation returns quick 202s between downloads; a quiet network is not a settled skyline.
      return !!scenery && (scenery.cataloguePending || scenery.pending > 0 || scenery.errors > 0
        || scenery.loaded !== scenery.total)
        || [...benchFetches.values()].some(start => performance.now() - start > 1500);
    },
    // Loading only: per-frame draw counts legitimately alternate (the river reflection's update cadence).
    signature: () => [renderer.info.memory.geometries, renderer.info.memory.textures,
      (renderer as unknown as { _pipelines?: { caches?: Map<unknown, unknown> } })._pipelines?.caches?.size].join('|'),
    info: () => ({ url: location.search, pose: window.__qaPose, settle: window.__game!.qa,
      scenery: distantCity?.progress,
      drafts: [...coarse.entries].filter(([, e]) => e.state === 'draft').map(([id, e]) => ({ id, pass: e.pass ?? null })),
      pixelRatio: renderer.getPixelRatio(), viewport: [innerWidth, innerHeight], devicePixelRatio, three: THREE.REVISION }),
  }, params.bench, params.quality) : null;
  if (bench) benchSettled = () => bench.settled;
  const publishBench = (result: Record<string, unknown> | null) => {
    if (!result) return;
    window.__bench = result;
    console.info(`BENCH ${JSON.stringify(result)}`);
    const out = document.createElement('pre');
    out.id = 'bench-result';
    out.style.cssText = 'position:fixed;left:8px;top:8px;max-width:60vw;max-height:90vh;overflow:auto;margin:0;padding:8px;'
      + 'background:rgba(0,0,0,.75);color:#fff;font:11px/1.3 monospace;z-index:10;user-select:all';
    out.textContent = JSON.stringify(result, null, 1);
    document.body.appendChild(out);
  };
  let stable = 0;
  let t0 = performance.now();
  let settleStart = clock.simSeconds;
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
    settleStart = clock.simSeconds;
    perf.reset();
  };
  window.__dev = {
    setPose: window.__qaMove,
    setHour: hour => { dayClock.setHour(hour); window.__qaMove!(window.__qaPose!); },
    pause: paused => { window.__render!.beforeFrame = paused ? () => false : undefined; },
    ...(bench ? { bench: (id: string, at: string) => {
      const pose = benchPose(id, at);
      bench.restart(pose);
      window.__bench = undefined;
      document.getElementById('bench-result')?.remove();
      window.__render!.beforeFrame = undefined;
      dayClock.set(pose.time);
      window.__qaMove!({ E: pose.qa.E, N: pose.qa.N, H: pose.qa.H,
        yaw: pose.qa.yawDeg, pitch: pose.qa.pitchDeg, fov: pose.qa.fovDeg });
    } } : {}),
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
    if (bench && window.__qaReady && !window.__qaError) bench.frame().then(publishBench).catch(fail);
    if (window.__qaReady || window.__qaError) return;
    const progress = coarse.progressAt(camera.position.x, camera.position.z);
    if (progress.errors.length) { window.__qaError = progress.errors.join('; '); return; }
    if (materials?.ground?.errors.length) { window.__qaError = materials.ground.errors[0]; return; }
    stable = progress.total > 0 && progress.loaded === progress.total && coarse.detailedAt(camera.position.x, camera.position.z)
      && !materials?.ground?.pending ? stable + 1 : 0;
    // Allow temporal colour history to converge after textures finish uploading (QA contract: >= 2 frames).
    if (stable >= 32) {
      window.__qaReady = true;
      window.__game!.qa = { loadMs: Math.round(performance.now() - t0), visTris: tiles.residentTris, models: tiles.residentModels, sunElevation: env.sunElevation, sunAzimuth: env.sunAzimuth };
    }
    if (clock.simSeconds - settleStart > 120) {
      window.__qaError = 'timeout: tiles did not settle in 120 s of frame time; '
        + JSON.stringify({ near: progress, detailed: coarse.detailedAt(camera.position.x, camera.position.z),
          groundTexturesPending: materials?.ground?.pending ?? 0, scenery: distantCity?.progress, status: coarse.status });
    }
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
    loadingScreen, dayClock, syncClock: time => dayClock.synchronize(time), sharedNow: () => dayClock.sharedNow(), clockTime: () => dayClock.time,
    trafficClock: () => dayClock.trafficClock(),
    setClock: date => dayClock.restore(date),
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
