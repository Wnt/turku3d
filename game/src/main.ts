import { placementVisibility } from './world/placementVisibility';
import { paceYields } from './render/yieldPacer';
import { streamingAllowance } from './render/preparationBudget';
import { DriveBench, loadDrive, driveQaPose, driveEpoch, driveClockMode, type LoadedDrive } from './qa/drive';
import { WeatherClock, type WeatherName } from './render/weather/schedule';
import { Rain } from './render/weather/rain';
import { weatherUniforms } from './render/weather/surfaces';
import { collisionPreview } from './world/collisionPreview';
// Turku3D game skeleton entry. Modes:
//   play (default): third-person player + car, physics worker, collision streaming, debug HUD
//   QA (?cam=E,N,H,yaw,pitch,fov[&roll=R]&time=ISO[&pass=id]): the qa/README.md §2 render contract; exact camera
//   (vertical fov), no HUD/player/physics; sets window.__qaReady/__qaError/__qaPose/__qaInfo/__qaProject[/__qaIdTable]
//   bench (?bench=camera&id=<street camera>[&at=HH:MM][&quality=]): QA with a street camera's pose at the play-mode
//   pixel ratio; after settling, warms and measures fixed frames and sets window.__bench (qa/bench.ts)
//   bench lab (?bench=static|install|fly[&tiles=][&cam=][&off=]): isolated scenarios over the real streamer and renderer;
//   window.__bench is the lab status with .result (qa/lab, tools/perf/README.md)
import { loading } from './core/loading';
import { installFetchRetry } from './core/retryFetch';
import { LoadingScreen } from './ui/loadingScreen';
import * as THREE from 'three/webgpu';
import { parseParams } from './core/params';
import { enToEngine, headingToYaw } from './core/origin';
import { adapterInfo, createRenderer, defaultBackend } from './render/renderer';
import { DayClock, type ClockMode } from './render/dayClock';
import { Environment, tileShadows } from './render/environment';
import { riverClock } from './render/riverWaves';
import { PostProcessing } from './render/post';
import { StreetLights } from './render/lights';
import { PointCloudOverlay, pointCloudParam } from './render/pointCloud';
import { PerformanceMonitor, type PerfSnapshot } from './render/performance';
import { AdaptiveResolution } from './render/adaptiveResolution';
import { QUALITY, deviceQuality, renderPixelRatio, renderTier, setRenderTier } from './render/quality';
import { gpuProbe } from './render/gpuProbe';
import { SettingGovernor } from './render/settingGovernor';
import { addTreePlacements } from './world/treePlacements';
import { VegetationLod } from './world/vegetationLod';
import { loadNatureLibrary } from './world/nature/library';
import { guardFrame } from './render/frameGuard';
import { PropLod } from './world/propLod';
import { MaterialStyler } from './render/materials';
import { modelTextures, textureUploads } from './render/textureUploads';
import { GraphicsPreparation, type PreparationPriority } from './render/graphicsPreparation';
import { precompileDissolve } from './render/lodFade';
import { blockLodRoots } from './world/buildingPlacements';
import { VisualTiles } from './world/tiles';
import { findInitialGround } from './world/startGround';
import { draftManifest, type Manifest } from './world/manifest';
import { CITIES, CITY, cityPageUrl, isHome, setCity, type CityName } from './core/city';
import { cityTitle, showTransition, travelProgress } from './gameplay/travel';
import { OWN_VEHICLE_ID } from './gameplay/missions/missions';
import { CollisionStreamer, STARTUP_COLLISION_RADIUS } from './world/collisionStreaming';
import { CoarseTiles, type CoarseHost } from './world/coarseTiles';
import { memoryCensus } from './world/memoryCensus';
import { readCol, readColSync } from './physics/colTile';
import { PhysicsClient } from './physics/client';
import { IdPass } from './render/idPass';
import { Telemetry, httpTransport, telemetryEnabled } from './telemetry';
import { BuildBadge, type BuildStatus } from './ui/buildBadge';
import { startPlay, type PlayWorld } from './play';
import { worldLoad } from './world/loadProgress';
import { retireAfterFrame, frameSubmitted } from './render/retirement';
import { CameraBench, benchPose } from './qa/bench';
import { FrameClock } from './core/frameClock';
import { ScenePopulation } from './world/scenePopulation';
import { GroundIndex } from './world/groundIndex';
import { drawCensus, uniformCensus } from './render/census';
import { GameRenderer } from './render/shadowMaterials';
import { StaticBundles } from './render/staticBundles';
import { freezeModel } from './world/frozenTransforms';
import { materialBandSort } from './render/drawSort';
import { altitudeShadows } from './render/shadowAltitude';
import { ReflectionScope } from './render/reflectionScope';
import { ablateScene, ablateSettings } from './qa/lab/ablate';
import { applyLodOverrides, lodSettings } from './qa/lab/overrides';
import { applyWaterSwitches } from './render/waterSwitches';
import { BenchLab } from './qa/lab/lab';
import { saveLabResult, startLabRecording } from './qa/lab/record';

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
      setWeather: (name: WeatherName | 'live', seconds?: number) => void; weather: WeatherClock; readonly rainEffects: Rain | null;
      landmark: (buildingId: string, url: string | null) => Promise<unknown>;
      geometryState: () => Promise<unknown>;
      coplanarDebug: (report: { findings: { kind: string; mesh: string; other_mesh?: string }[] } | null) => Promise<unknown>;
      pause: (paused: boolean) => void; bench?: (id: string, at: string) => void };
    __render?: { camera: THREE.PerspectiveCamera; environment: Environment; renderer: THREE.WebGPURenderer;
      perf: PerformanceMonitor; post: PostProcessing | null; setTime: (iso: string) => void; setHour: (hour: number) => void;
      beforeFrame?: () => boolean; afterFrame?: () => void; visibility?: typeof placementVisibility;
      /** One frame's draws by pass/group plus the node-program build log (render/census.ts). */
      census?: () => Promise<unknown>; bundles?: StaticBundles | null;
      /** One frame's uniform-buffer writes by pass, object family, group and uniform (render/census.ts). */
      uniforms?: () => Promise<unknown>;
      /** Retained bytes by category: geometry, texture data, streaming caches, collision cells (world/memoryCensus.ts). */
      memory?: () => Promise<unknown> };
    /** Tile streaming on demand: the loaded tile ids (the HUD shows only their count). */
    __tiles?: { loaded: () => string[] };
  }
}

// Transient asset failures (a share deploy restarting its server) retry before anything loads.
installFetchRetry();
let params: ReturnType<typeof parseParams>;
try {
  params = parseParams(location.search, navigator);
} catch (e) {
  window.__qaError = String((e as Error).message ?? e); // bad cam/time/pass: the QA driver stops waiting
  throw e;
}
// ?water= river technique A/B switches (render/waterSwitches.ts), before any river material exists.
try { applyWaterSwitches(location.search); } catch (e) { window.__qaError = String((e as Error).message ?? e); throw e; }
// Lab LOD overrides and ablations set engine constants, quality-tier fields and AA before anything reads them.
if (params.lab) applyLodOverrides(params.lab.lod, params.quality, params);
if (params.lab) ablateSettings(params.lab.off, params.quality, params, new URLSearchParams(location.search).has('aa'));
let initialDrive: LoadedDrive | null = null;
if (params.drive) {
  window.__bench = { phase: 'loading', done: false, replayOf: params.drive.capture, segment: params.drive.segment };
  try {
    initialDrive = await loadDrive(params.drive.capture, params.drive.segment);
    params.qa = driveQaPose(initialDrive.poses[0]); params.motion = true;
    // ?view=live replays the scene content but renders with this machine's own window, resolution path and tier.
    const recorded = params.drive.view === 'live' ? ['traffic', 'walkers', 'trafficDensity'] as const
      : ['traffic', 'walkers', 'trafficDensity', 'quality', 'aa'] as const;
    if (initialDrive.meta.params) {
      for (const key of recorded) if (!new URLSearchParams(location.search).has(key)) (params as any)[key] = initialDrive.meta.params[key];
    }
  } catch (e) { window.__qaError = String(e); Object.assign(window.__bench!, { phase: 'error', error: String(e) }); throw e; }
}
// Lower-end GPUs default to the reduced tier (render/quality.ts deviceQuality); ?quality= always wins. Play and
// ?view=live replays only: QA, the bench lab and recorded replays keep their deterministic tier.
{
  const given = new URLSearchParams(location.search).has('quality'), live = params.drive?.view === 'live';
  const detect = !given && (!params.qa || live);
  const adapter = params.backend === 'webgpu' ? await adapterInfo() : null;
  const gpu = adapter && { vendor: adapter.vendor, architecture: adapter.architecture };
  const cores = navigator.hardwareConcurrency ?? null;
  const tier = detect ? deviceQuality(gpu, params.quality, adapter?.probe?.ms, cores) : params.quality;
  if (tier !== params.quality) loading.note(`Quality ${tier} for ${gpu?.vendor} ${gpu?.architecture}${adapter?.probe ? ` (probe ${adapter.probe.ms.toFixed(2)} ms)` : ''}`);
  setRenderTier({ tier, gpu, cores, probe: adapter?.probe ?? undefined,
    source: given ? 'url' : params.drive && !live ? 'recorded' : tier !== params.quality ? 'device' : 'default' });
  params.quality = tier;
}
const loadingScreen = new LoadingScreen(!!params.qa);
/** Bench probe: requests in flight with their start times, so a slow download keeps the scene unsettled. */
const benchFetches = new Map<number, number>();
if (params.bench || params.drive || params.lab) {
  const original = globalThis.fetch.bind(globalThis);
  let next = 0;
  globalThis.fetch = (...args: Parameters<typeof fetch>) => {
    const id = next++;
    benchFetches.set(id, performance.now());
    return original(...args).finally(() => benchFetches.delete(id));
  };
}
/** QA renders 1:1 with the viewport; the bench probe measures at the play-mode pixel ratio (adaptive scale 1). */
const qaCanvas = !!params.qa && !params.bench && !params.drive && !params.lab;
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
// A stray rejection is optional work that failed (a decorative asset, a warmup): logged, never a failed load. Required
// boot steps reach fail() through runPlay/runQa/runLab, and a top-level await rejection is an 'error' event.
addEventListener('unhandledrejection', (e) => { errors.push(String(e.reason)); console.warn(`[boot] unhandled rejection: ${String(e.reason)}`); });

const idPass = params.qa && params.pass === 'id' ? new IdPass() : null;
const { renderer, backend } = await loading.track('GPU renderer initialization', createRenderer(await defaultBackend(params.backend, params.backendGiven), document.body,
  { qa: qaCanvas, idPass: !!idPass, mobile: params.mobile, quality: params.quality }));
const perf = new PerformanceMonitor(renderer, backend);
// A page kept in the back-forward cache holds its whole WebGPU device. On a 4 GB GPU the next game page then pages
// VRAM every frame (Radeon Pro 560X: ~20 ms of GPU idle per frame; 22 -> 58 FPS once the working set fit), so leaving
// releases the device and coming back from the cache reloads.
addEventListener('pagehide', event => { if (event.persisted) (renderer.backend as { device?: GPUDevice }).device?.destroy(); });
addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
// Startup GPU throughput probe for the GPUs adapterInfo() did not probe (Apple's chooses the tier there): recorded in
// renderTier() and captures, to calibrate tiering on other vendors.
const probeDevice = (renderer.backend as { device?: GPUDevice }).device;
if (probeDevice && !renderTier()?.probe && (!params.qa || params.drive?.view === 'live')) void gpuProbe(probeDevice).then(probe => {
  const tier = renderTier();
  if (tier) setRenderTier({ ...tier, probe });
  if (probe) loading.note(`GPU probe ${probe.ms.toFixed(2)} ms (${probe.method})`);
});
const scene = new THREE.Scene();
/** ?bench=drive&view=live: play's window and adaptive pixel ratio instead of the recorded ones. */
const liveDrive = params.drive?.view === 'live';
if (liveDrive && initialDrive) initialDrive.settings = { pixelRatio: null, viewport: null };
let driveViewport: number[] | null = initialDrive?.settings.viewport ?? params.lab?.size ?? null;
let drivePixelRatio: number | null = initialDrive?.settings.pixelRatio ?? params.lab?.dpr ?? null;
if (driveViewport) renderer.setSize(driveViewport[0], driveViewport[1], false);
if (drivePixelRatio) renderer.setPixelRatio(drivePixelRatio);
const camera = new THREE.PerspectiveCamera(60, driveViewport ? driveViewport[0] / driveViewport[1] : innerWidth / innerHeight, 0.1, 6000);
camera.rotation.order = 'YXZ';
const env = new Environment(scene, renderer, params.quality);
if (params.lab) ablateScene(params.lab.off, renderer, env.sun);
/** QA and ?time= views stay fixed (local only); play follows the shared UTC cycle, which the server aligns. */
const clockMode: ClockMode = initialDrive ? driveClockMode(initialDrive.meta) : params.hour !== null ? { kind: 'hour', hour: params.hour }
  : params.qa || params.timeGiven ? { kind: 'date', date: params.time } : { kind: 'cycle', periodMs: params.cycleMinutes * 60_000 };
const clock = new FrameClock(initialDrive ? driveEpoch(initialDrive) : params.time.getTime());
const dayClock = new DayClock(env, clockMode, !params.qa, () => params.qa ? clock.utcMs : Date.now());
const weather = new WeatherClock(() => dayClock.sharedNow(), undefined, params.qa ? 'partly cloudy' : undefined);
let rain = idPass ? null : new Rain(scene, params.quality === 'low' ? 2500 : 6000);
window.__dev = { geometryState: () => import('./qa/geometryDebug').then(m => m.geometryState(scene)), coplanarDebug: report => import('./qa/geometryDebug').then(m => m.coplanarDebug(scene, report)), landmark: (buildingId, url) => import('./qa/landmarkPreview').then(m => m.previewLandmark(scene, buildingId, url)), get rainEffects() { return rain; }, setPose: pose => window.__qaMove?.(pose), setHour: hour => dayClock.setHour(hour),
  pause: paused => { window.__render!.beforeFrame = paused ? () => false : undefined; },
  setWeather: (name, seconds) => weather.setWeather(name, seconds), weather };
if (!idPass) env.updateIBL();
const lights = idPass || params.lab?.off.has('lights') ? null : new StreetLights(scene, params.quality, params.lights, env.lamps.point);
const weatherOverlay = rain ? { overlay: (r: THREE.WebGPURenderer, c: THREE.Camera, depth: THREE.Texture) => {
  const weatherLayer = rain!.overlay(r, c, depth), lightLayer = lights?.sprites.overlay(r, c, depth);
  return { color: lightLayer ? weatherLayer.color.add(lightLayer.color) : weatherLayer.color,
    resource: { dispose() { weatherLayer.resource.dispose(); lightLayer?.resource.dispose(); } } };
} } : lights?.sprites;
// LiDAR overlay (render/pointCloud): built hidden in desktop play for the editor to show; ?pointcloud=1 shows it at
// startup (also in QA views), ?pointcloud=0 leaves it out of the pipeline.
const pointCloudWanted = pointCloudParam();
const pointCloud = idPass || !pointCloudWanted.build || (!pointCloudWanted.show && (params.qa || params.mobile || params.lab || params.drive))
  ? null : new PointCloudOverlay(params.quality);
if (pointCloud && pointCloudWanted.show) { pointCloud.setColorMode(pointCloudWanted.mode); pointCloud.setVisible(true); }
let post = idPass || params.lab?.off.has('post') ? null : new PostProcessing(renderer, scene, camera, params.quality, params.aa, weatherOverlay, pointCloud);
Object.defineProperty(window, '__perf', { get: () => perf.snapshot() });
window.__render = { camera, environment: env, renderer, perf, post, visibility: placementVisibility,
  census: () => {
    let previous: (() => void) | undefined;
    return drawCensus(renderer, {
      install: each => { previous = window.__render!.afterFrame; window.__render!.afterFrame = () => { previous?.(); each(); }; },
      uninstall: () => { window.__render!.afterFrame = previous; },
    }, renderer instanceof GameRenderer ? renderer.builds : undefined);
  }, uniforms: () => {
    let previous: (() => void) | undefined;
    return uniformCensus(renderer, {
      install: each => { previous = window.__render!.afterFrame; window.__render!.afterFrame = () => { previous?.(); each(); }; },
      uninstall: () => { window.__render!.afterFrame = previous; },
    });
  }, memory: () => memoryCensus({ scene, renderer, coarse: censusSources.coarse, collision: censusSources.collision, ground: censusSources.ground }),
  setTime: (iso: string) => {
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) throw new Error('invalid render time');
  dayClock.set(date);
}, setHour: (hour: number) => {
  if (!Number.isFinite(hour)) throw new Error('invalid render hour');
  dayClock.setHour(hour);
} };
let natureLibrary = idPass || params.lab?.off.has('vegetation') ? null : loadNatureLibrary(renderer, env, !!params.qa && !params.motion);
let vegetation = natureLibrary ? new VegetationLod(params.quality, natureLibrary, scene) : null;
// Tree shadow casters are selected against the sun's cascade volumes, not the view.
if (vegetation) vegetation.casters = () => env.casterVolumes();
const furniture = idPass || params.lab?.off.has('props') ? null : new PropLod();
const graphics = new GraphicsPreparation(scene, model => post?.prepare(model), release => retireAfterFrame(renderer, release));
(window.__render as unknown as Record<string, unknown>).graphics = () => graphics.stats();
const tiles = new VisualTiles(!!idPass, release => graphics.retire(release));
// Static render bundles (render/staticBundles.ts) are an A/B alternative, off by default: per-tile bundles measured a
// net loss (bench=static: CPU p50 +0.9 ms, +120 draws). Opt in with ?bundles=1 (lab, drive replays and play).
// Owner perf3; removed unless the per-cell variant wins its A/B. WebGPU only.
const bundlesWanted = new URLSearchParams(location.search).get('bundles') === '1';
const bundles = idPass || backend !== 'WebGPU' || !bundlesWanted ? null : new StaticBundles(scene);
// GTA V's altitude rule: small casters leave the sun's cascades above about 100 m (render/shadowAltitude.ts).
// ?altitudeShadows=0 keeps them, for the bench=fly A/B (owner perf3; the losing path goes after it).
altitudeShadows.enabled = new URLSearchParams(location.search).get('altitudeShadows') !== '0';
// River reflection renders skip subtrees they cannot draw (render/reflectionScope.ts); ?reflectionScope=0 for the A/B.
if (renderer instanceof GameRenderer && new URLSearchParams(location.search).get('reflectionScope') !== '0') {
  const scope = renderer.reflectionScope = new ReflectionScope();
  tiles.events.addEventListener('load-model', ({ scene: model }: { scene: THREE.Object3D }) => scope.add(model));
  tiles.events.addEventListener('dispose-model', ({ scene: model }: { scene: THREE.Object3D }) => scope.remove(model));
}

// ?drawSort=material groups opaque draws by material within 25 m depth bands (render/drawSort.ts, A/B).
if (new URLSearchParams(location.search).get('drawSort') === 'material')
  (renderer as unknown as { setOpaqueSort(sort: ReturnType<typeof materialBandSort>): void }).setOpaqueSort(materialBandSort());
// ?tileShadows=chunks: every cascade draws tile shadows per 62.5 m chunk (the A/B against whole-tile outer cascades).
tileShadows.chunksEverywhere = new URLSearchParams(location.search).get('tileShadows') === 'chunks';
/** Ground height under the camera for the altitude rule: the lowest content of the tile there, else sea level. */
let groundBelow: (x: number, z: number) => number = () => 0;
window.__render!.bundles = bundles;
tiles.events.addEventListener('load-model', ({ scene: model }: { scene: THREE.Object3D }) => bundles?.add(model));
// The world root never moves, so it does not force every object's matrices each frame; published models freeze their
// static transforms (world/frozenTransforms.ts). ?frozen=0 keeps the old per-frame recompose, for the A/B.
const frozenModels = new URLSearchParams(location.search).get('frozen') !== '0';
if (frozenModels) {
  scene.updateMatrix(); scene.matrixAutoUpdate = false;
  tiles.events.addEventListener('load-model', ({ scene: model }: { scene: THREE.Object3D }) => freezeModel(model));
}
tiles.events.addEventListener('dispose-model', ({ scene: model }: { scene: THREE.Object3D }) => bundles?.remove(model));
tiles.events.addEventListener('load-model', ({ scene: model }: { scene: THREE.Object3D }) => rain?.exposure.add(model));
const materials = idPass ? null : new MaterialStyler(renderer, env, params.quality, !!params.qa && !params.motion);
tiles.events.addEventListener('dispose-model', ({ scene: model }: { scene: THREE.Object3D }) => {
  furniture?.remove(model);
  rain?.exposure.remove(model);
  graphics.retire(() => { vegetation?.remove(model); materials?.release(model); });
});
const queueGraphics = (model: THREE.Object3D, prepare: () => void | Promise<unknown>, signal?: AbortSignal,
  priority: PreparationPriority = 'tile') => {
  // The menu warms its initial view only. Backfill other resident programs after it becomes ready.
  if (!params.qa && worldLoad.snapshot().phase !== 'ready') {
    graphics.defer(model, prepare, signal);
    return Promise.resolve();
  }
  return graphics.enqueue(model, prepare, signal, false, priority);
};
// Each model's preparation also compiles the dissolve twins it can fade with (render/lodFade.ts), so no dissolve ever
// builds a shader or pipeline inside a frame: a full tile's whole model (it dissolves in where no cell of its own
// draws, and holds its block LOD shells), other models' block shells, the level's bias tiers of a scenery cell.
const prepareGraphics = (model: THREE.Object3D, signal?: AbortSignal, priority: PreparationPriority = 'tile') =>
  queueGraphics(model, async () => {
    await materials?.prepare(model, scene, camera, signal);
    const roots = model.userData.dissolveIn ? [model] : blockLodRoots(model);
    // Twins of surfaces the river reflects also draw there (in-frame reflection builds of landmark/roof twins).
    if (roots.length && post) await precompileDissolve(roots, [0], async root => {
      await post!.prepare(root);
      await materials?.prepareReflections(root, scene, camera);
    });
  }, signal, priority);
/** Car batches and keepers (actors/fleet/visuals.ts) take the actor lane ahead of queued tile preparations. */
const prepareActorGraphics = (model: THREE.Object3D) => prepareGraphics(model, undefined, 'actor');
// Distant scenery is excluded from river-reflection layers; warm only its actual main-view passes.
const prepareSceneryGraphics = (model: THREE.Object3D, signal?: AbortSignal) => queueGraphics(model, async () => {
  await Promise.all([...modelTextures(model)].map(map => textureUploads(renderer).enqueue(map)));
  const tiers = model.userData.dissolveTiers as number[] | undefined;
  if (tiers && post) await precompileDissolve([model], tiers, root => post!.prepare(root));
}, signal, 'scenery');
// ?resolutionTarget=40: desktop AdaptiveResolution aims at the 40 FPS goal instead of 60, so it lowers resolution only
// when frames miss the goal (at 60 every desktop sits at the 0.5 floor). A/B, owner winperf: decided by M1 Max and
// Radeon 560X ?view=live replays; the losing path goes within ten merges.
const resolution = new AdaptiveResolution(params.mobile ? 30 : new URLSearchParams(location.search).get('resolutionTarget') === '40' ? 40 : 60);
// Rendering-only steps for tiers below medium when frames miss the goal at the resolution floor (render/settingGovernor.ts),
// least visible first. ?governor=0 keeps the tier fixed: A/B, owner winperf, decided by Windows and Air live replays;
// the losing path goes within ten merges.
const sceneryRadius = params.quality === 'low' || params.quality === 'mobile' ? 2500 : 4000;
const governedVegetation = QUALITY[params.quality].vegetation as { near: number; mid: number };
const vegetationAsConfigured = { ...governedVegetation };
const governor = QUALITY[params.quality].governor && new URLSearchParams(location.search).get('governor') !== '0' ? new SettingGovernor([
  { name: 'distant city 2500 m', apply: () => distantCity?.setRadius(Math.min(sceneryRadius, 2500)), undo: () => distantCity?.setRadius(sceneryRadius) },
  { name: 'trees to impostors sooner', apply: () => Object.assign(governedVegetation, { near: Math.min(vegetationAsConfigured.near, 35), mid: Math.min(vegetationAsConfigured.mid, 70) }),
    undo: () => Object.assign(governedVegetation, vegetationAsConfigured) },
  { name: 'small casters drop sun shadows', apply: () => { altitudeShadows.forced = true; }, undo: () => { altitudeShadows.forced = false; } },
]) : null;
/** Set by the bench probe (runQa): true once its scene has settled. */
let benchSettled = () => false;
// During play r186's asynchronous shader-build stages run after each frame within a 3 ms share instead of back to
// back between two frames (render/yieldPacer.ts), but only once the 3x3 window has its full tiles: while it waits
// (streamingAllowance.urgent) they run at native speed. Pacing those too starved HD installs on the Radeon 560X
// (tile.prepare p50 2 -> 27 s, HD 62 -> 34 %) and cost the M1 Air 5 points. Loading and bench settles are not paced.
paceYields(() => ((!params.qa && worldLoad.snapshot().phase === 'ready') || benchSettled()) && !streamingAllowance.urgent);
let distantCity: CoarseTiles['screenshotScenery'] | null = null;
let lastWeatherIBL = -Infinity, lastWeatherCover = -1, lastWeatherOvercast = -1;
function refreshWeatherIBL(sample: { cloudCover: number; overcast: number }) {
  if (idPass) return;
  const now = performance.now();
  if (now - lastWeatherIBL < 2500) return;
  if (Math.abs(sample.cloudCover - lastWeatherCover) < .04 && Math.abs(sample.overcast - lastWeatherOvercast) < .04) return;
  lastWeatherIBL = now; lastWeatherCover = sample.cloudCover; lastWeatherOvercast = sample.overcast;
  env.updateIBL();
}
if (import.meta.hot) import.meta.hot.accept('./render/post', next => {
  if (!next || idPass) return;
  const previous = post;
  post = new next.PostProcessing(renderer, scene, camera, params.quality, params.aa, weatherOverlay, pointCloud);
  window.__render!.post = post;
  graphics.retire(() => previous?.dispose());
});
if (import.meta.hot) import.meta.hot.accept('./render/weather/rain', next => {
  if (!next || !rain) return;
  const previous = rain; previous.dispose(true);
  const replacement = new next.Rain(scene, params.quality === 'low' ? 2500 : 6000, previous.exposure, previous.scene, previous.sceneDepth.value as THREE.DepthTexture);
  replacement.setPhaseSeconds(previous.fixedPhaseSeconds);
  rain = replacement;
});
const renderFrame = () => {
  if ((liveDrive || (!params.qa && worldLoad.snapshot().phase === 'ready')) && document.visibilityState === 'visible') {
    const scale = resolution.update(performance.now(), params.mobile ? perf.frameCostMs : undefined);
    const ratio = renderPixelRatio(innerWidth, innerHeight, devicePixelRatio, false, params.mobile, QUALITY[params.quality].maxPixels) * scale;
    if (Math.abs(renderer.getPixelRatio() - ratio) > 0.001) renderer.setPixelRatio(ratio);
    const stepped = governor?.update(performance.now(), scale <= 0.5 + 1e-9);
    if (stepped) console.info(`[governor] level ${governor!.level}: ${stepped.name}`);
  }
  dayClock.update();
  const weatherSample = weather.update();
  env.setWeather(weatherSample);
  weatherUniforms.wetness.value = weatherSample.wetness;
  weatherUniforms.rain.value = weatherSample.rain;
  weatherUniforms.seconds.value = (dayClock.sharedNow() / 1000) % 4096;
  rain?.update(camera, weatherSample, dayClock.sharedNow() / 1000, env.nightFactor);
  refreshWeatherIBL(weatherSample);
  if (params.qa || worldLoad.snapshot().phase === 'ready') distantCity?.follow(camera);
  // QA fixes weather independently of the HUD; players share UTC-driven cloud drift.
  const sharedSeconds = dayClock.sharedNow() / 1000;
  env.atmosphere.setTime(params.qa ? 0 : sharedSeconds);
  riverClock.seconds = params.qa ? (params.motion ? clock.simSeconds : 0) : sharedSeconds;
  vegetation?.setTime(riverClock.seconds);
  // Warmup renders every shadow pipeline whatever the hour; the cascades freeze at night only once play starts
  // (and in the bench probe once its scene has settled, so its night frame costs what play's does).
  env.updateShadows((!params.qa && worldLoad.snapshot().phase === 'ready') || benchSettled());
  post?.grade(env.sample);
  if (vegetation) vegetation.screenshotDistance = env.screenshotDistance;
  vegetation?.update(camera);
  furniture?.update(camera);
  lights?.update(camera, renderer, { time: dayClock.time, sunElevation: env.sunElevation, weather: weatherSample, fogFar: env.sample.fogFar });
  // Turku's LiDAR only: after a switch to another city its nodes would stream in the wrong frame.
  if (isHome()) pointCloud?.update(camera, renderer);
  if (!idPass) placementVisibility.update(camera);
  bundles?.update();
  const ground = groundBelow(camera.position.x, camera.position.z);
  altitudeShadows.update(camera.position.y - (Number.isFinite(ground) ? ground : 0));
  // A render that throws must not leave a pass's target and MRT on the renderer (render/frameGuard.ts).
  guardFrame(renderer, scene, () => { if (post) post.render(); else renderer.render(scene, camera); });
  // Models prepared before the first shadow pass compile their casters now, in the paced preparation queue.
  if (post) for (const model of post.takeShadowless())
    graphics.enqueue(model, () => {}, undefined, true).catch(error => console.warn(`[shadow preparation] ${error}`));
  frameSubmitted(renderer);
};

addEventListener('resize', () => {
  camera.aspect = driveViewport ? driveViewport[0] / driveViewport[1] : innerWidth / innerHeight;
  if (params.qa && !params.drive) applyQaCamera();
  camera.updateProjectionMatrix();
  renderer.setPixelRatio(drivePixelRatio ?? renderPixelRatio(innerWidth, innerHeight, devicePixelRatio, qaCanvas, params.mobile, QUALITY[params.quality].maxPixels) * (params.qa && !liveDrive ? 1 : resolution.scale));
  renderer.setSize(driveViewport?.[0] ?? innerWidth, driveViewport?.[1] ?? innerHeight, !driveViewport);
  env.resize();
});

// Test clock: rendered frames and simulated seconds (60 Hz physics steps in play mode, frame time clamped like the
// play loop's dt in QA mode). Browser checks budget their waits in these units, so a starved host makes a run
// slower instead of failing it (tests/e2e/waits.ts).
if (import.meta.env.DEV) void import('./dev/buildNotice').then(m => m.startBuildNotice());
window.__game = { dayClock, backend, params, errors, tiles, clock, resolution, governor, three: THREE.REVISION, loadProgress: () => worldLoad.snapshot(),
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
    next.casters = () => env.casterVolumes();
    vegetation = next;
    window.__game!.vegetation = next;
    for (const model of models) await next.addAsync(model);
  };
  vegetationReplacement = vegetationReplacement.then(install, install);
  return vegetationReplacement;
};

// QA has no play session to replace; actor edits are applied through its runtime inspection hooks.
if (import.meta.hot && params.qa) import.meta.hot.accept('./play', () => {});
if (params.lab) runLab().catch(fail);
else if (params.qa) runQa().catch(fail);
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
  camera.aspect = driveViewport ? driveViewport[0] / driveViewport[1] : innerWidth / innerHeight;
  camera.near = 0.1;
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
}

/** `?gpuBudget=GiB` as bytes; undefined (the tier's budget) when absent or not a number. */
function gpuBudgetParam(value: string | null) {
  const gib = value === null || value.trim() === '' ? NaN : Number(value);
  return Number.isFinite(gib) && gib >= 0 ? gib * 2 ** 30 : undefined;
}

/** What `__render.memory()` reads once the world exists. */
const censusSources: { coarse: CoarseTiles | null; collision: CollisionStreamer | null; ground: GroundIndex | null } = { coarse: null, collision: null, ground: null };

async function draftWorld() {
  const physics = new PhysicsClient();
  physics.onError = e => fail(`physics: ${e}`);
  const rapier = await physics.ready;
  const city = await cityWorld(physics, PARK_CITY ? cityLayer(CITY.name) : scene);
  return { physics, rapier, city, ...city };
}

/**
 * A/B (owner: coordinator; reason: in-place return time vs memory; measured in the city-switch round trips; removed
 * within 10 merges after 2026-11-05, the losing path with it): `?parkCity` keeps the city just left parked (its tile
 * models in a layer out of the scene, collision unloaded, streaming stopped) and resumes it on the way back instead
 * of streaming it again. One parked city at most. Without it every switch releases the old city (leaveCity).
 */
const PARK_CITY = new URLSearchParams(location.search).has('parkCity');

/** A city's own scene layer (parkCity): its tile models and scenery, which a parked city takes out of the scene. */
function cityLayer(name: CityName) {
  const layer = new THREE.Group();
  layer.name = `city:${name}`;
  scene.add(layer);
  return layer;
}

/** A city's own world: its manifest, collision streamer and tile streamer. `layer` holds its tile models (the
 *  scene itself, or with the parkCity A/B a group of its own that a parked city leaves). */
interface CityWorld { name: CityName; manifest: Manifest; streamer: CollisionStreamer; coarse: CoarseTiles; layer: THREE.Object3D;
  /** Aborted when the city is released: its queued graphics preparations (tiles, scenery cells, trees) are dropped
   *  instead of holding the shared preparation queue, and the released city, until they reach the front. */
  life: AbortController;
  /** A parked city's next collision streamer (the old one unloaded every cell when the city was left). */
  restream(): CollisionStreamer }

/** The active city's world, new (a city switch builds one) or `parked` (the parkCity A/B: the city left last). */
async function cityWorld(physics: PhysicsClient, layer: THREE.Object3D = scene, parked?: CityWorld): Promise<CityWorld> {
  if (parked) {
    const streamer = parked.restream();
    scene.add(parked.layer);
    parked.coarse.unpark();
    adoptCoarse(parked.coarse);
    for (const e of parked.coarse.entries.values()) if (e.model) { await vegetation?.addAsync(e.model); furniture?.add(e.model); }
    censusSources.coarse = parked.coarse; censusSources.collision = streamer;
    window.__tiles = { loaded: () => parked.coarse.loadedTiles() };
    return { ...parked, streamer };
  }
  const manifest = draftManifest(location.href);
  const life = new AbortController();
  const holder = { streamer: new CollisionStreamer(manifest, physics, params.collisionRadius) };
  const coarse = await draftVisuals(manifest, {
    unloadCollision: id => physics.unloadTile(id), refreshCollision: ids => holder.streamer.refresh(ids),
    checkCollision: buffer => physics.checkTile(buffer),
    retainCollision: (id, entry, buffer) => holder.streamer.retain(id, entry, buffer),
  }, layer, life.signal);
  censusSources.coarse = coarse; censusSources.collision = holder.streamer;
  window.__tiles = { loaded: () => coarse.loadedTiles() };
  return { name: CITY.name, manifest, streamer: holder.streamer, coarse, layer, life,
    restream: () => (holder.streamer = new CollisionStreamer(manifest, physics, params.collisionRadius)) };
}

/** Streams the active city's start (params.spawn) and finds supported ground for the player and the own car there
 *  (world/startGround.ts); the collision radius stays at the startup radius until the session releases its starts. */
async function placeStart(physics: PhysicsClient, streamer: CollisionStreamer, coarse: CoarseTiles) {
  // spawn: wait for the collision tile under the spawn point, then drop onto the ground
  const requested = enToEngine(params.spawn.E, params.spawn.N, 0);
  // Limit demand before the first update: otherwise the full 3x3 window is already in flight.
  streamer.radius = Math.min(params.collisionRadius, STARTUP_COLLISION_RADIUS);
  // The start's view wedge loads full detail with its ground: the player never starts looking at scenery cells.
  const startYaw = headingToYaw(params.spawn.headingDeg);
  coarse.preload([requested], streamer.radius, true, { x: requested.x, z: requested.z, dx: -Math.sin(startYaw), dz: -Math.cos(startYaw) });
  const tWait = performance.now();
  let placed: Awaited<ReturnType<typeof findInitialGround>> = null;
  while (!placed) {
    if (performance.now() - tWait > 600_000) throw new Error(`No supported street start after 10 minutes: ${coarse.status}`);
    coarse.update(requested.x, requested.z, undefined, undefined, false);
    streamer.update(requested.x, requested.z);
    const progress = coarse.progressAt(requested.x, requested.z), cells = streamer.progressAt(requested.x, requested.z);
    worldLoad.update({ visual: { loaded: progress.loaded, total: progress.total }, collision: { loaded: cells.loaded, total: cells.total } });
    loadingScreen.update(false, coarse.status);
    if (streamer.supports(requested.x, requested.z)) {
      physics.step(1 / 60, { vx: 0, vz: 0, jump: false }, null);
      placed = await findInitialGround(requested, coarse.roadGraphs(),
        (x, z) => physics.raycast({ x, y: 500, z }, { x: 0, y: -1, z: 0 }, 1000),
        (x, z, radius) => streamer.supports(x, z, radius),
        // Open ground once the start's collision is complete: no street near the spawn may support a car.
        streamer.missingNear(requested.x, requested.z, streamer.radius).length === 0 && !streamer.pending);
    }
    if (!placed) await new Promise(r => setTimeout(r, 100));
  }
  return placed;
}

async function draftVisuals(manifest: CoarseHost['manifest'], collision: Pick<CoarseHost,
  'unloadCollision' | 'refreshCollision' | 'checkCollision' | 'retainCollision'>, layer: THREE.Object3D = scene, signal?: AbortSignal) {
  const coarse = await CoarseTiles.start({ scene: layer as THREE.Scene, renderer, manifest,
    detailRadius: params.mobile ? 80 : undefined,
    vegetationDistance: QUALITY[params.quality].vegetation.mid,
    prepareVegetation: async (m,data) => { addTreePlacements(m,data); await vegetation?.addAsync(m); await prepareGraphics(m, signal); },
    previewCollision: collisionPreview, preparePreview: m => prepareGraphics(m, signal),
    // A/B (stream2, until the Air and M1 Max memory runs compare): ?keepArrays keeps CPU copies of uploaded geometry.
    keepArrays: !!idPass || new URLSearchParams(location.search).has('keepArrays'),
    // Measurement (stream2, Air vs 560X budgets): ?gpuBudget=GiB replaces the tier's GPU residency budget, 0 for none.
    gpuBudget: gpuBudgetParam(new URLSearchParams(location.search).get('gpuBudget')),
    // ?memBudget=GiB: the Apple reduced tier's heap + GPU total (default 2.5).
    memoryBudget: gpuBudgetParam(new URLSearchParams(location.search).get('memBudget')),
    ...collision,
    trackModel: (m, id) => tiles.trackModel(m, id), untrackModel: (m, id) => tiles.untrackModel(m, id),
    retire: release => graphics.retire(release),
    prepareGraphics: m => prepareSceneryGraphics(m, signal),
    style: async m => { await materials?.applyAsync(m); rain?.exposure.add(m); }, unstyle: m => { rain?.exposure.remove(m); vegetation?.remove(m); furniture?.remove(m); tiles.releasePreparedModel(m); materials?.release(m); },
    styled: async m => { await tiles.prepareModelAsync(m); await vegetation?.addAsync(m); furniture?.add(m); await prepareGraphics(m, signal); } });
  if (!coarse) throw new Error('World generation service is unavailable');
  adoptCoarse(coarse);
  if (!idPass) {
    distantCity!.setRadius(params.lab?.off.has('scenery') ? 0 : sceneryRadius);
    if (params.qa) distantCity!.follow(camera);
    // Keep the sky behind the far city; atmospheric haze still comes from the timecycle.
    env.setCityDistance(camera, 15000);
  }
  return coarse;
}

/** The world-level consumers of the active city's tile streamer (ground queries, street lights, distant scenery). */
function adoptCoarse(coarse: CoarseTiles) {
  groundBelow = (x, z) => coarse.groundBelow(x, z);
  lights?.attach(coarse);
  if (!idPass) { distantCity = coarse.screenshotScenery; window.__game!.scenery = distantCity; }
}

async function runQa() {
  const q = params.qa!;
  document.body.classList.add('qa');
  applyQaCamera();
  // Fixed cameras need the same validated world geometry, but no simulation worker or Rapier startup.
  const qaManifest = draftManifest(location.href);
  // The decoded collision cells also give ambient cars and walkers their ground (world/groundIndex.ts).
  const ground = new GroundIndex();
  const coarse = await draftVisuals(qaManifest, {
    unloadCollision: id => ground.remove(id), refreshCollision: () => {},
    checkCollision: async buffer => {
      const col = await readCol(buffer);
      if (!col.indices.length && !col.prims.length) throw new Error(`${col.id}: no collision geometry`);
      // Validated now; indexed on the first ground query of the cell (actors stand on few of them).
      ground.addLazy(col.id, buffer, bytes => readColSync(bytes, { checkCrc: false }));
    },
  });
  censusSources.coarse = coarse; censusSources.ground = ground;
  window.__tiles = { loaded: () => coarse.loadedTiles() };
  const population = !idPass && (!!params.drive || (params.traffic ?? 0) > 0 || (params.walkers ?? 0) > 0)
    ? new ScenePopulation(scene, coarse, camera, qaManifest, ground, { quality: params.quality, traffic: params.traffic,
      walkers: params.walkers, density: params.trafficDensity, hour: () => dayClock.hour, night: () => dayClock.night,
      prepare: prepareActorGraphics }) : null;
  if (population) { await population.load(); population.reset(clock.epochMs, 20261102); }
  // Drive-bench captures read the tile streamer here (stats.coarse, 1 Hz coverage snapshots), as in play sessions.
  window.__game!.population = population; window.__game!.coarse = coarse;
  let replaySeed = 20261102;
  window.__game!.replay = {
    reset: async ({ epochMs, seed = 20261102, mode }: { epochMs: number; seed?: number; mode?: ClockMode }) => {
      clock.reset(epochMs); dayClock.resetTimeline(mode); replaySeed = seed;
      await population?.prepare(epochMs, seed);
    },
    seek: (seconds: number) => clock.seek(seconds),
    snapshot: () => ({ seconds: clock.simSeconds, epochMs: clock.epochMs, seed: replaySeed, population: population?.snapshot() }),
  };
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
      return !!scenery && !scenery.ready
        || [...benchFetches.values()].some(start => performance.now() - start > 1500)
        || !!rain && weather.sample.wetness > .0001 && !rain.exposure.settled;
    },
    // Loading only: per-frame draw counts legitimately alternate (the river reflection's update cadence).
    signature: () => [renderer.info.memory.geometries, renderer.info.memory.textures,
      (renderer as unknown as { _pipelines?: { caches?: Map<unknown, unknown> } })._pipelines?.caches?.size].join('|'),
    info: () => ({ url: location.search, pose: window.__qaPose, settle: window.__game!.qa,
      scenery: distantCity?.progress,
      weather: weather.sample, rainExposure: rain?.exposure.progress,
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
  if (params.preview) void import('./qa/preview').then(m => m.startPreview(q, pose => window.__qaMove!(pose),
    () => coarse.loadedTiles())).catch(fail);
  window.__dev = {
    geometryState: () => import('./qa/geometryDebug').then(m => m.geometryState(scene)),
    coplanarDebug: report => import('./qa/geometryDebug').then(m => m.coplanarDebug(scene, report)),
    get rainEffects() { return rain; },
    setWeather: (name, seconds) => weather.setWeather(name, seconds), weather,
    landmark: (buildingId, url) => import('./qa/landmarkPreview').then(m => m.previewLandmark(scene, buildingId, url)),
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
  const driveBench = initialDrive ? new DriveBench({ scene, camera, game: window.__game!,
    configure: drive => {
      if (liveDrive) drive.settings = { pixelRatio: null, viewport: null };
      driveViewport = drive.settings.viewport; drivePixelRatio = drive.settings.pixelRatio;
      renderer.setPixelRatio(drivePixelRatio ?? renderPixelRatio(innerWidth, innerHeight, devicePixelRatio, false, params.mobile, QUALITY[params.quality].maxPixels) * (liveDrive ? resolution.scale : 1));
      renderer.setSize(driveViewport?.[0] ?? innerWidth, driveViewport?.[1] ?? innerHeight, false);
      camera.aspect = driveViewport ? driveViewport[0] / driveViewport[1] : innerWidth / innerHeight; camera.updateProjectionMatrix();
      env.resize();
    },
    signature: () => `${renderer.info.memory.geometries}|${renderer.info.memory.textures}|${(renderer as any)._pipelines?.caches?.size}`,
    busy: () => {
      const p = coarse.progressAt(camera.position.x, camera.position.z);
      return !p.total || p.loaded !== p.total || !coarse.detailedAt(camera.position.x, camera.position.z) || !!materials?.ground?.pending || [...benchFetches.values()].some(start => performance.now() - start > 1500) || !!distantCity && !distantCity.progress.ready;
    },
    reset: async drive => {
      await (window.__game!.replay as any).reset({ epochMs: driveEpoch(drive), seed: drive.meta.replay?.seed ?? 20261102,
        mode: driveClockMode(drive.meta) });
    },
    seek: seconds => clock.seek(seconds), begin: () => perf.reset(),
  }) : null;
  if (driveBench) {
    window.__game!.driveBench = driveBench; benchSettled = () => driveBench.settled;
    await driveBench.load(initialDrive!);
  }
  renderer.setAnimationLoop(() => {
    if (window.__render?.beforeFrame?.() === false) return;
    driveBench?.before();
    const now = performance.now();
    clock.tick(now);
    perf.begin();
    coarse.update(camera.position.x, camera.position.z, now, undefined, true, camera.position.y);
    population?.update(clock.simSeconds);
    renderFrame();
    perf.end();
    window.__render?.afterFrame?.();
    driveBench?.after();
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
    if (!driveBench && clock.simSeconds - settleStart > 120) {
      window.__qaError = 'timeout: tiles did not settle in 120 s of frame time; '
        + JSON.stringify({ near: progress, detailed: coarse.detailedAt(camera.position.x, camera.position.z),
          groundTexturesPending: materials?.ground?.pending ?? 0, scenery: distantCity?.progress, status: coarse.status });
    }
  });
}

// ------------------------------------------------------------------------------------------- bench lab
async function runLab() {
  const lab = params.lab!;
  document.body.classList.add('qa');
  applyQaCamera();
  // The QA world: validated draft tiles and scenery, no physics worker. The lab pauses the tile scheduler and
  // installs its tiles through the streamer's own preview and detail paths (qa/lab/lab.ts).
  const labManifest = draftManifest(location.href);
  const ground = new GroundIndex();
  const coarse = await draftVisuals(labManifest, {
    unloadCollision: id => ground.remove(id), refreshCollision: () => {},
    checkCollision: async buffer => {
      const col = await readCol(buffer);
      if (!col.indices.length && !col.prims.length) throw new Error(`${col.id}: no collision geometry`);
      // Validated now; indexed on the first ground query of the cell (actors stand on few of them).
      ground.addLazy(col.id, buffer, bytes => readColSync(bytes, { checkCrc: false }));
    },
  });
  const population = !idPass && (params.traffic !== 0 || params.walkers !== 0)
    ? new ScenePopulation(scene, coarse, camera, labManifest, ground, { quality: params.quality, traffic: params.traffic,
      walkers: params.walkers, density: params.trafficDensity, hour: () => dayClock.hour, night: () => dayClock.night,
      prepare: prepareActorGraphics }) : null;
  if (population) await population.load();
  const game = window.__game!;
  const pipelines = () => (renderer as unknown as { _pipelines?: { caches?: Map<unknown, unknown> } })._pipelines?.caches?.size ?? null;
  let clockStart: number | null = null;
  const look = new THREE.Vector3(), focus = new THREE.Vector3();
  Object.assign(game, { coarse, population, orbit: { yaw: 0, pitch: 0 }, input: { actions: {}, lookX: 0, lookY: 0 },
    state: () => {
      const p = camera.position;
      return { mode: 'bench', paused: false, drivenId: null, inputSource: 'bench', E: p.x + 23459500, N: 6704500 - p.z, H: p.y,
        feet: [p.x, p.y, p.z], vel: [0, 0, 0], camera: { yaw: 0 }, vehicle: null, visModels: tiles.residentModels,
        visTris: tiles.residentTris, tilesSettled: true, frameMs: null };
    } });
  const benchLab = new BenchLab({
    lab, quality: params.quality, aa: params.aa, camera, coarse, renderer, perf,
    inFrameBuilds: () => renderer instanceof GameRenderer ? renderer.builds.inFrameTotal : 0,
    signature: () => `${renderer.info.memory.geometries}|${renderer.info.memory.textures}|${pipelines()}`,
    busy: () => !!distantCity && !distantCity.progress.ready || !!materials?.ground?.pending
      || [...benchFetches.values()].some(start => performance.now() - start > 1500),
    resetWorld: async () => {
      clock.reset(params.time.getTime()); clockStart = performance.now(); dayClock.resetTimeline();
      await population?.prepare(clock.epochMs, lab.seed);
      clockStart = performance.now();
    },
    subsystems: { streaming: [coarse, 'update'], population: population ? [population, 'update'] : undefined,
      vegetation: vegetation ? [vegetation, 'update'] : undefined, props: furniture ? [furniture, 'update'] : undefined,
      lights: lights ? [lights, 'update'] : undefined, visibility: [placementVisibility, 'update'],
      render: post ? [post, 'render'] : [renderer, 'render'] },
    pause: paused => { window.__render!.beforeFrame = paused ? () => false : undefined; },
    record: meta => startLabRecording(game, meta),
    saveResult: (id, result) => saveLabResult(id, result),
    lod: () => lodSettings(params.quality, params),
    info: () => ({ backend, three: THREE.REVISION, commit: import.meta.env.VITE_COMMIT ?? null,
      pixelRatio: renderer.getPixelRatio(), viewport: [innerWidth, innerHeight], devicePixelRatio,
      drawingBuffer: [renderer.domElement.width, renderer.domElement.height], scenery: distantCity?.progress ?? null,
      pipelines: pipelines(), resident: { models: tiles.residentModels, tris: tiles.residentTris } }),
  });
  window.__bench = benchLab.status as unknown as Record<string, unknown>;
  benchSettled = () => !['loading', 'load', 'settle'].includes(benchLab.status.phase);
  renderer.setAnimationLoop(() => {
    if (window.__render?.beforeFrame?.() === false) return;
    const now = performance.now();
    benchLab.before(now);
    if (clockStart !== null) clock.seek((now - clockStart) / 1000);
    clock.tick(now);
    // The sun's cascades centre ahead of the camera, as play centres them on the player.
    env.follow(focus.copy(camera.position).add(camera.getWorldDirection(look).multiplyScalar(40)));
    perf.begin();
    coarse.update(camera.position.x, camera.position.z, now, undefined, !lab.off.has('scenery'), camera.position.y);
    population?.update(clock.simSeconds);
    renderFrame();
    perf.end();
    window.__render?.afterFrame?.();
    benchLab.after();
  });
  const status = await benchLab.run();
  if (status.error) { console.error(`BENCH LAB ${status.error}`); window.__qaError = status.error; }
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
  const { physics, rapier, manifest, streamer, coarse, city: booted } = await draftWorld();
  let current: CityWorld = booted, parked: CityWorld | null = null;
  // Keep startup's live worker/streaming diagnostics available before the play session is ready.
  Object.assign(window.__game!, { physics, coarse, streamer });
  const playWorld = async (city: Pick<CityWorld, 'manifest' | 'streamer' | 'coarse'>, first: boolean): Promise<PlayWorld> => {
    const { manifest, streamer, coarse } = city;
    const { spawn, car: parking } = await placeStart(physics, streamer, coarse);
    const groundAt = async (x: number, z: number) => (await physics.raycast({ x, y: 500, z }, { x: 0, y: -1, z: 0 }, 1000))?.point.y ?? spawn.y;
    physics.spawnCharacter({ x: spawn.x, y: spawn.y + 0.05, z: spawn.z });
    const yaw0 = headingToYaw(params.spawn.headingDeg);
    // The own car (OWN_VEHICLE_ID): spawned at boot, carried over (reset to the new start) on a city switch.
    if (first) physics.spawnVehicle({ x: parking.x, y: parking.y + 0.75, z: parking.z }, parking.yaw);
    else physics.resetVehicle({ x: parking.x, y: parking.y + 0.75, z: parking.z }, parking.yaw, OWN_VEHICLE_ID);
    return { params, backend, renderer, scene, camera, env, perf, tiles, vegetation: () => vegetation, lights, materials, physics, rapier, manifest,
      streamer, coarse, telemetry, clock, errors, fail, hudEl, renderFrame, prepareGraphics: prepareActorGraphics, groundAt,
      loadingScreen, dayClock, weather, syncClock: time => dayClock.synchronize(time), sharedNow: () => dayClock.sharedNow(), clockTime: () => dayClock.time,
      trafficClock: () => dayClock.trafficClock(),
      setClock: date => dayClock.restore(date),
      spawn: { ...spawn, yaw: yaw0 }, car: { x: parking.x, y: parking.y, z: parking.z, yaw: parking.yaw }, travel };
  };
  let world: PlayWorld;
  /**
   * In-place city switch (gameplay/travel.ts): the play session ends (its ticket in session storage carries the trip),
   * the old city's tiles, collision and vehicles go, the engine origin moves to the new city (core/city.ts setCity),
   * its world streams in around the start (the arrival portal's exit pose in Turku), and a new session takes the
   * ticket. One page, one renderer, one physics world; the URL follows (?city=, ?spawn=) for reloads and links.
   */
  let travelling: Promise<void> | null = null;
  function travel(to: CityName, spawn?: { E: number; N: number; headingDeg: number }) {
    travelling ??= (async () => {
      const t0 = performance.now();
      const kept = session.dispose(true);
      // The session's own transition went with its DOM; this one stays, with the streaming progress, until the new
      // session runs, then fades out over the first frames.
      const curtain = showTransition(document.body, CITIES[to]);
      curtain.classList.add('shown');
      const line = curtain.querySelector<HTMLElement>('.travel-progress')!;
      const progress = setInterval(() => { line.textContent = travelProgress(worldLoad.snapshot()); }, 250);
      for (const v of physics.latest?.vehicles ?? []) if (v.id && v.id !== OWN_VEHICLE_ID) physics.removeVehicle(v.id);
      const leaving = current;
      await leaving.streamer.dispose();
      if (PARK_CITY) {
        // One parked city at most: the one parked before goes for good.
        if (parked && parked !== leaving) { parked.life.abort(); parked.coarse.leaveCity(); }
        leaving.coarse.park();
        for (const e of leaving.coarse.entries.values()) if (e.model) { vegetation?.remove(e.model); furniture?.remove(e.model); }
        leaving.layer.removeFromParent();
        parked = leaving;
      } else { leaving.life.abort(); leaving.coarse.leaveCity(); }
      setCity(to);
      history.replaceState(history.state, '', cityPageUrl(to, location, spawn));
      params.spawnGiven = !!spawn;
      params.spawn = spawn ?? { E: CITY.spawn.E, N: CITY.spawn.N, headingDeg: CITY.spawn.headingDeg };
      worldLoad.update({ phase: 'loading', errors: [] });
      const reuse = parked?.name === to ? parked : undefined;
      if (reuse) parked = null;
      const city = current = await cityWorld(physics, PARK_CITY ? cityLayer(to) : scene, reuse);
      Object.assign(window.__game!, { coarse: city.coarse, streamer: city.streamer });
      world = await playWorld(city, false);
      session = await startPlay(world, null, { audio: kept.audio, socket: kept.socket });
      worldLoad.update({ phase: 'ready' });
      graphics.flush(fail);
      clearInterval(progress);
      document.title = cityTitle();
      curtain.classList.remove('shown');
      setTimeout(() => curtain.remove(), 700);
      console.info(`[travel] ${CITY.name} ready in ${((performance.now() - t0) / 1000).toFixed(1)} s`);
    })().catch(fail).finally(() => { travelling = null; });
    return travelling;
  }
  world = await playWorld({ manifest, streamer, coarse }, true);
  // Localhost game (Vite dev server, tools/dev/localhost-game): a landing that touches only the play session swaps it in
  // place; the world above keeps the player, the vehicles and the loaded tiles. The build badge names main's commit
  // and its verification verdict. Production builds drop this (import.meta.hot is undefined there).
  const badge = import.meta.hot ? new BuildBadge(document.body) : null;
  let session = await startPlay(world, null);
  worldLoad.update({ phase: 'ready' });
  graphics.flush(fail);
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
        graphics.flush(fail);
        badge.applied('hot-swap');
      }).catch(fail);
    });
  }
}
