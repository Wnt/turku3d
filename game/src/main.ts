// Turku3D game skeleton entry. Modes:
//   play (default): third-person player + car, physics worker, collision streaming, debug HUD
//   QA (?cam=E,N,H,yaw,pitch,fov[&roll=R]&time=ISO[&pass=id]): the qa/README.md §2 render contract; exact camera
//   (vertical fov), no HUD/player/physics; sets window.__qaReady/__qaError/__qaPose/__qaInfo/__qaProject[/__qaIdTable]
import * as THREE from 'three/webgpu';
import { parseParams } from './core/params';
import { enToEngine, collisionTileAt, headingToYaw } from './core/origin';
import { createRenderer, defaultBackend } from './render/renderer';
import { Environment } from './render/environment';
import { PostProcessing } from './render/post';
import { PerformanceMonitor, type PerfSnapshot } from './render/performance';
import { renderPixelRatio } from './render/quality';
import { MaterialStyler } from './render/materials';
import { VisualTiles } from './world/tiles';
import { loadManifest, type Manifest } from './world/manifest';
import { CollisionStreamer } from './world/collisionStreaming';
import { CoarseTiles } from './world/coarseTiles';
import { PhysicsClient } from './physics/client';
import { IdPass } from './render/idPass';
import { Telemetry, httpTransport, telemetryEnabled } from './telemetry';
import { BuildBadge, type BuildStatus } from './ui/buildBadge';
import { startPlay, type PlayWorld } from './play';

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
const hudEl = document.getElementById('hud')!;
const errors: string[] = [];
const fail = (e: unknown) => {
  const msg = String((e as Error)?.stack ?? e);
  errors.push(msg);
  console.error(msg);
  hudEl.textContent = `ERROR: ${msg}`;
  window.__qaError = msg;
};
addEventListener('error', (e) => errors.push(String(e.message)));
addEventListener('unhandledrejection', (e) => errors.push(String(e.reason)));

const idPass = params.qa && params.pass === 'id' ? new IdPass() : null;
const { renderer, backend } = await createRenderer(await defaultBackend(params.backend, params.backendGiven), document.body,
  { qa: !!params.qa, idPass: !!idPass });
const perf = new PerformanceMonitor(renderer, backend);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.1, 6000);
camera.rotation.order = 'YXZ';
const env = new Environment(scene, renderer, params.quality);
env.setTime(params.time);
if (!idPass) env.updateIBL();
const post = idPass ? null : new PostProcessing(renderer, scene, camera, params.quality, params.aa);
Object.defineProperty(window, '__perf', { get: () => perf.snapshot() });
/** The sun clock: the ?time= start, the time slider or a loaded game's clock. */
let clockTime = params.time;
window.__render = { camera, environment: env, renderer, perf, setTime: (iso: string) => {
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) throw new Error('invalid render time');
  clockTime = date;
  env.setTime(date);
  if (!idPass) env.updateIBL();
} };
const renderFrame = () => { if (post) post.render(); else renderer.render(scene, camera); };
const tiles = new VisualTiles(params.tileset, renderer, camera, scene, !!idPass);
const materials = idPass ? null : new MaterialStyler(renderer, env, params.quality, !!params.qa, params.tileset);
tiles.tiles.addEventListener('load-model', ({ scene: model }: { scene: THREE.Object3D }) => materials?.apply(model));
tiles.tiles.addEventListener('dispose-model', ({ scene: model }: { scene: THREE.Object3D }) => materials?.release(model));

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
window.__game = { backend, params, errors, tiles, clock, three: THREE.REVISION };

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

async function runQa() {
  const q = params.qa!;
  document.body.classList.add('qa');
  applyQaCamera();
  window.__qaPose = { E: q.E, N: q.N, H: q.H, yaw: q.yawDeg, pitch: q.pitchDeg, roll: q.rollDeg, fov: q.fovDeg };
  window.__qaProject = (E: number, N: number, H: number) => {
    const p = enToEngine(E, N, H);
    const v = new THREE.Vector3(p.x, p.y, p.z).applyMatrix4(camera.matrixWorldInverse);
    if (v.z >= -1e-6) return null; // behind the camera
    v.applyMatrix4(camera.projectionMatrix);
    return [((v.x + 1) / 2) * innerWidth, ((1 - v.y) / 2) * innerHeight];
  };
  const loadedTiles: string[] = [];
  tiles.tiles.addEventListener('load-model', ({ scene: model, tile }: { scene: THREE.Object3D; tile: { extras?: { id?: string } } }) => {
    const id = tile?.extras?.id ?? null;
    if (id) loadedTiles.push(id);
    idPass?.apply(model, id);
  });
  if (idPass) {
    IdPass.prepare(renderer, scene, [env.sky]);
    window.__qaIdTable = idPass.table;
  }
  window.__qaInfo = { renderer: `three.js r${THREE.REVISION} WebGPURenderer`, backend, pass: params.pass,
    time: params.timeGiven ? params.time.toISOString() : null, three: THREE.REVISION, tiles: loadedTiles, tileset: params.tileset,
    quality: params.quality, effectiveTime: params.time.toISOString(), sseUnits: 'drawing-buffer-pixels' };
  env.follow(camera.position.clone().add(camera.getWorldDirection(new THREE.Vector3()).multiplyScalar(40)));
  let stable = 0;
  let idleEmptySince: number | null = null;
  const bounds = new THREE.Box3();
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
    idleEmptySince = null;
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
    tiles.update(camera);
    renderFrame();
    perf.end();
    window.__render?.afterFrame?.();
    if (window.__qaReady || window.__qaError) return;
    if (tiles.errors.length) { window.__qaError = tiles.errors[0]; return; }
    if (materials?.ground?.errors.length) { window.__qaError = materials.ground.errors[0]; return; }
    stable = tiles.settled && !materials?.ground?.pending ? stable + 1 : 0;
    // contract: a pose outside the loaded data reports __qaError instead of running into the 120 s timeout. Only when
    // nothing loaded for ~2 s although the tileset is idle, and the camera stands outside the tileset footprint.
    const empty = tiles.tiles.root && tiles.loadedModels === 0 && tiles.tiles.loadProgress >= 1;
    idleEmptySince = empty ? idleEmptySince ?? performance.now() : null;
    if (idleEmptySince !== null && performance.now() - idleEmptySince >= 2000 && tiles.tiles.getBoundingBox(bounds)) {
      bounds.applyMatrix4(tiles.tiles.group.matrixWorld);
      const c = camera.position;
      if (c.x < bounds.min.x || c.x > bounds.max.x || c.z < bounds.min.z || c.z > bounds.max.z) {
        window.__qaError = `pose outside the tileset: camera E ${q.E} N ${q.N}, nothing in view loaded`;
        return;
      }
    }
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
  const telemetry = telemetryEnabled(location, navigator.webdriver)
    ? new Telemetry(httpTransport(`${location.origin}/telemetry`)) : null;
  if (telemetry) {
    tiles.tiles.addEventListener('load-model', ({ scene: model, tile }: { scene: THREE.Object3D; tile: { extras?: { id?: string } } }) =>
      telemetry.addModel(model, tile?.extras?.id));
    tiles.tiles.addEventListener('dispose-model', ({ scene: model }: { scene: THREE.Object3D }) => telemetry.removeModel(model));
    addEventListener('pagehide', () => telemetry.flush(true));
    addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') telemetry.flush(true); });
  }
  const physics = new PhysicsClient();
  physics.onError = (e) => fail(`physics: ${e}`);
  const [rapier, manifest] = await Promise.all([physics.ready, loadManifest(params.manifest) as Promise<Manifest>]);
  const streamer = new CollisionStreamer(manifest, physics, params.collisionRadius);
  telemetry?.setBuiltTiles(Object.keys(manifest.visual));
  // Localhost only: draft tiles from tools/dev/coarse-tiles beyond the published tileset (null elsewhere).
  const coarse = await CoarseTiles.start({ scene, renderer, manifest, manifestUrl: params.manifest, tiles: tiles.tiles,
    unloadCollision: id => physics.unloadTile(id), style: m => materials?.apply(m), unstyle: m => materials?.release(m) });

  // spawn: wait for the collision tile under the spawn point, then drop onto the ground
  const selected = params.spawnGiven ? undefined : manifest.spawn;
  const spawn = selected ? { x: selected.position[0], y: selected.position[1], z: selected.position[2] } : enToEngine(params.spawn.E, params.spawn.N, 0);
  const spawnId = (() => { const t = collisionTileAt(spawn.x, spawn.z); return `c_${t.i}_${t.j}`; })();
  const required = [spawnId];
  if (selected) {
    const t = collisionTileAt(selected.vehicle.position[0], selected.vehicle.position[2]);
    required.push(`c_${t.i}_${t.j}`);
  }
  streamer.update(spawn.x, spawn.z, true);
  const tWait = performance.now();
  while (required.some(id => !physics.loaded.has(id) && manifest.collision[id])) {
    if (performance.now() - tWait > 30000) throw new Error(`spawn collision tile ${spawnId} did not load`);
    await new Promise((r) => setTimeout(r, 20));
    streamer.update(spawn.x, spawn.z);
  }
  // the worker needs one world step before queries see new colliders
  physics.step(1 / 60, { vx: 0, vz: 0, jump: false }, null);
  const groundAt = async (x: number, z: number) => (await physics.raycast({ x, y: 500, z }, { x: 0, y: -1, z: 0 }, 1000))?.point.y ?? manifest.collision[spawnId]?.h[1] ?? 0;
  spawn.y = await groundAt(spawn.x, spawn.z);
  physics.spawnCharacter({ x: spawn.x, y: spawn.y + 0.05, z: spawn.z });
  const yaw0 = headingToYaw(selected?.headingDeg ?? params.spawn.headingDeg);
  // Published starts use mapped parking. Explicit/debug spawns retain a nearby car.
  const carPos = selected ? { x: selected.vehicle.position[0], z: selected.vehicle.position[2] } : { x: spawn.x + 6 * Math.cos(yaw0), z: spawn.z - 6 * Math.sin(yaw0) };
  const carGround = await groundAt(carPos.x, carPos.z);
  const carYaw = selected?.vehicle.yaw ?? yaw0 + Math.PI / 2;
  physics.spawnVehicle({ x: carPos.x, y: carGround + 0.75, z: carPos.z }, carYaw);
  for (const parked of manifest.vehicles ?? []) {
    physics.spawnVehicle({ x: parked.x, y: parked.y + 0.75, z: parked.z }, parked.yaw - Math.PI / 2,
      { id: parked.object_id, parked: true,
        mass: Math.round(1200 * parked.width * parked.length * parked.height / (1.85 * 4.55 * 1.48)),
        halfExt: { x: parked.length / 2 - 0.18, y: Math.min(0.55, parked.height * 0.32), z: parked.width / 2 - 0.05 } });
  }
  const world: PlayWorld = { params, backend, renderer, scene, camera, env, perf, tiles, materials, physics, rapier, manifest,
    streamer, coarse, telemetry, clock, errors, fail, hudEl, renderFrame, groundAt,
    clockTime: () => clockTime, setClock: date => window.__render!.setTime(date.toISOString()),
    spawn: { ...spawn, yaw: yaw0 }, car: { x: carPos.x, y: carGround, z: carPos.z, yaw: carYaw } };
  // Localhost game (Vite dev server, tools/dev/localhost-game): a landing that touches only the play session swaps it in
  // place; the world above keeps the player, the vehicles and the loaded tiles. The build badge names main's commit
  // and its verification verdict. Production builds drop this (import.meta.hot is undefined there).
  const badge = import.meta.hot ? new BuildBadge(document.body) : null;
  let session = await startPlay(world, null);
  if (import.meta.hot && badge) {
    // A new tile release under /tiles (a data landing) reloads the page; the autosave keeps the game.
    let release: string | null | undefined;
    const onStatus = (status: BuildStatus) => {
      badge.set(status);
      const key = status.tiles?.key ?? null;
      if (release === undefined) release = key;
      else if (key !== release && params.tileset.startsWith('/tiles/')) session.reload();
    };
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
