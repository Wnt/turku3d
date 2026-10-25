// Turku3D game skeleton entry. Modes:
//   play (default): third-person player + car, physics worker, collision streaming, debug HUD
//   QA (?cam=E,N,H,yaw,pitch,fov[&roll=R]&time=ISO[&pass=id]): the qa/README.md §2 render contract; exact camera
//   (vertical fov), no HUD/player/physics; sets window.__qaReady/__qaError/__qaPose/__qaInfo/__qaProject[/__qaIdTable]
import * as THREE from 'three/webgpu';
import { parseParams } from './core/params';
import { enToEngine, engineToEN, collisionTileAt, headingToYaw, yawToHeading } from './core/origin';
import { Input } from './input';
import { createRenderer } from './render/renderer';
import { Environment } from './render/environment';
import { VisualTiles } from './world/tiles';
import { loadManifest, type Manifest } from './world/manifest';
import { CollisionStreamer } from './world/collisionStreaming';
import { PhysicsClient } from './physics/client';
import { PlayerVisual, SPEED } from './actors/player';
import { CarVisual } from './actors/car';
import { OrbitCamera } from './actors/camera';
import { Hud } from './ui/hud';
import { IdPass } from './render/idPass';
import { MATERIALS } from './shared/col/materials';
import type { VehicleInput } from './physics/protocol';

declare global {
  interface Window {
    __qaReady?: boolean;
    __qaError?: string;
    __qaPose?: { E: number; N: number; H: number; yaw: number; pitch: number; roll: number; fov: number };
    __qaInfo?: Record<string, unknown>;
    __qaIdTable?: Record<string, unknown>;
    __qaProject?: (E: number, N: number, H: number) => [number, number] | null;
    __game?: Record<string, unknown>;
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
const { renderer, backend } = await createRenderer(params.backend, document.body, { qa: !!params.qa, idPass: !!idPass });
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.1, 6000);
camera.rotation.order = 'YXZ';
const env = new Environment(scene, !!params.qa);
env.setTime(params.time);
const tiles = new VisualTiles(params.tileset, renderer, camera, scene);

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  if (params.qa) applyQaCamera();
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

window.__game = { backend, params, errors, tiles, three: THREE.REVISION };

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
    time: params.timeGiven ? params.time.toISOString() : null, three: THREE.REVISION, tiles: loadedTiles, tileset: params.tileset };
  env.follow(camera.position.clone().add(camera.getWorldDirection(new THREE.Vector3()).multiplyScalar(40)));
  let stable = 0, idleEmpty = 0;
  const bounds = new THREE.Box3();
  const t0 = performance.now();
  renderer.setAnimationLoop(() => {
    tiles.update(camera);
    renderer.render(scene, camera);
    if (window.__qaReady || window.__qaError) return;
    if (tiles.errors.length) { window.__qaError = tiles.errors[0]; return; }
    stable = tiles.settled ? stable + 1 : 0;
    // contract: a pose outside the loaded data reports __qaError instead of running into the 120 s timeout. Only when
    // nothing loaded for ~2 s although the tileset is idle, and the camera stands outside the tileset footprint.
    idleEmpty = tiles.tiles.root && tiles.loadedModels === 0 && tiles.tiles.loadProgress >= 1 ? idleEmpty + 1 : 0;
    if (idleEmpty >= 120 && tiles.tiles.getBoundingBox(bounds)) {
      bounds.applyMatrix4(tiles.tiles.group.matrixWorld);
      const c = camera.position;
      if (c.x < bounds.min.x || c.x > bounds.max.x || c.z < bounds.min.z || c.z > bounds.max.z) {
        window.__qaError = `pose outside the tileset: camera E ${q.E} N ${q.N}, nothing in view loaded`;
        return;
      }
    }
    // ready after the tileset settled for 10 consecutive frames (textures transcoded and uploaded; contract: >= 2)
    if (stable >= 10) {
      window.__qaReady = true;
      window.__game!.qa = { loadMs: Math.round(performance.now() - t0), visTris: tiles.residentTris, models: tiles.residentModels, sunElevation: env.sunElevation, sunAzimuth: env.sunAzimuth };
    }
    if (performance.now() - t0 > 120000) window.__qaError = 'timeout: tiles did not settle in 120 s';
  });
}

// ------------------------------------------------------------------------------------------- play mode
async function runPlay() {
  const input = new Input(renderer.domElement);
  const hud = new Hud(hudEl);
  if (!params.hud) hud.toggle();
  const physics = new PhysicsClient();
  physics.onError = (e) => fail(`physics: ${e}`);
  const [rapier, manifest] = await Promise.all([physics.ready, loadManifest(params.manifest) as Promise<Manifest>]);
  const streamer = new CollisionStreamer(manifest, physics, params.collisionRadius);
  const player = new PlayerVisual(scene, origin => physics.raycast(origin, { x: 0, y: -1, z: 0 }, 1.3, 'foot'));
  const car = new CarVisual(scene);
  const orbit = new OrbitCamera(camera);
  const playerLoad = player.load(`${import.meta.env.BASE_URL}assets/character/mannequin.glb`);

  // spawn: wait for the collision tile under the spawn point, then drop onto the ground
  const spawn = enToEngine(params.spawn.E, params.spawn.N, 0);
  const spawnId = (() => { const t = collisionTileAt(spawn.x, spawn.z); return `c_${t.i}_${t.j}`; })();
  streamer.update(spawn.x, spawn.z, true);
  const tWait = performance.now();
  while (!physics.loaded.has(spawnId) && manifest.collision[spawnId]) {
    if (performance.now() - tWait > 30000) throw new Error(`spawn collision tile ${spawnId} did not load`);
    await new Promise((r) => setTimeout(r, 20));
    streamer.update(spawn.x, spawn.z);
  }
  // the worker needs one world step before queries see new colliders
  physics.step(1 / 60, { vx: 0, vz: 0, jump: false }, null);
  const groundAt = async (x: number, z: number) => (await physics.raycast({ x, y: 500, z }, { x: 0, y: -1, z: 0 }, 1000))?.point.y ?? manifest.collision[spawnId]?.h[1] ?? 0;
  spawn.y = await groundAt(spawn.x, spawn.z);
  physics.spawnCharacter({ x: spawn.x, y: spawn.y + 0.05, z: spawn.z });
  const yaw0 = headingToYaw(params.spawn.headingDeg);
  orbit.yaw = yaw0;
  // car parked 6 m to the right of the spawn, facing the spawn heading (chassis +X forward -> yaw + 90 deg)
  const carPos = { x: spawn.x + 6 * Math.cos(yaw0), z: spawn.z - 6 * Math.sin(yaw0) };
  const carGround = await groundAt(carPos.x, carPos.z);
  const carYaw = yaw0 + Math.PI / 2;
  physics.spawnVehicle({ x: carPos.x, y: carGround + 0.75, z: carPos.z }, carYaw);
  await playerLoad;

  let mode: 'foot' | 'car' = 'foot';
  let paused = false;
  let wasGrounded = false;
  const desiredVelocity = new THREE.Vector2();
  const feet = new THREE.Vector3(spawn.x, spawn.y, spawn.z);
  const vel = new THREE.Vector3();
  let grounded = false, groundMat = 0;
  let frameEma = 16.7, physEma = 0;
  let last = performance.now();
  let camRayBusy = false;
  const tmp = new THREE.Vector3();
  const carQuat = new THREE.Quaternion();

  const state = () => {
    const en = engineToEN(feet.x, feet.y, feet.z);
    const v = physics.latest?.vehicle;
    return { mode, paused, locomotion: player.locomotion, contacts: player.contacts, visualFeet: player.root.position.toArray(), cameraYaw: orbit.yaw, inputSource: input.source, feet: feet.toArray(), vel: vel.toArray(), grounded, groundMaterial: groundMat, E: en.E, N: en.N, H: en.H,
      vehicle: v ? { pos: [v.pos.x, v.pos.y, v.pos.z], speed: v.speed } : null, collision: physics.totals(), visTris: tiles.residentTris,
      visModels: tiles.residentModels, tilesSettled: tiles.settled, frameMs: frameEma, errors };
  };
  Object.assign(window.__game!, { input, state, physics, rapier, ready: true });

  renderer.setAnimationLoop(() => {
    const now = performance.now();
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    frameEma += (dt * 1000 - frameEma) * 0.05;

    input.update(dt);
    if (input.consume('hud')) hud.toggle();
    if (input.consume('pause')) paused = !paused;
    if (!paused) orbit.input(input.lookX, input.lookY, input.zoom);
    if (!paused && input.consume('cameraReset')) {
      if (mode === 'foot') orbit.yaw = player.root.rotation.y - Math.PI;
      else if (physics.latest?.vehicle) {
        const q = physics.latest.vehicle.rot;
        const forward = tmp.set(1, 0, 0).applyQuaternion(carQuat.set(q.x, q.y, q.z, q.w));
        orbit.yaw = Math.atan2(-forward.x, -forward.z);
      }
      orbit.pitch = -0.25;
    }
    const { moveY: fwd, moveX: side } = input.actions;

    const vs = physics.latest?.vehicle;
    if (!paused && input.consume('interact')) {
      if (mode === 'foot' && vs && Math.hypot(vs.pos.x - feet.x, vs.pos.z - feet.z) < 4) {
        mode = 'car';
        physics.setCharacterEnabled(false);
        player.root.visible = false;
        orbit.distance = 8;
      } else if (mode === 'car' && vs) {
        mode = 'foot';
        carQuat.set(vs.rot.x, vs.rot.y, vs.rot.z, vs.rot.w);
        const out = tmp.set(0, 0, -(1.0 + 0.9)).applyQuaternion(carQuat); // driver side (-Z in the chassis frame)
        physics.setCharacterEnabled(true, { x: vs.pos.x + out.x, y: vs.pos.y + 0.2, z: vs.pos.z + out.z });
        player.root.visible = true;
        orbit.distance = 4.5;
      }
    }
    if (mode === 'car' && !paused && input.consume('vehicleReset') && vs) {
      const fwdV = tmp.set(1, 0, 0).applyQuaternion(carQuat.set(vs.rot.x, vs.rot.y, vs.rot.z, vs.rot.w));
      physics.resetVehicle({ x: vs.pos.x, y: vs.pos.y + 1.5, z: vs.pos.z }, Math.atan2(-fwdV.z, fwdV.x));
    }

    let charIn = { vx: 0, vz: 0, jump: false };
    let carIn: VehicleInput | null = null;
    if (mode === 'foot') {
      const b = orbit.basis();
      const mx = b.fx * fwd + b.rx * side, mz = b.fz * fwd + b.rz * side;
      const speed = input.sprinting ? SPEED.sprint : input.actions.walk > 0.5 ? SPEED.walk : SPEED.run;
      const blend = 1 - Math.exp(-dt * (Math.hypot(mx, mz) > 0 ? 14 : 22));
      desiredVelocity.lerp(new THREE.Vector2(mx * speed, mz * speed), blend);
      charIn = { vx: desiredVelocity.x, vz: desiredVelocity.y, jump: input.consume('jump') };
    } else {
      desiredVelocity.set(0, 0);
      // Negative throttle uses the physics vehicle's brake-then-reverse behavior.
      carIn = { throttle: input.actions.throttle - input.actions.brake, steer: -input.actions.steer,
        brake: Math.min(input.actions.throttle, input.actions.brake), handbrake: input.actions.handbrake > 0.5 };
    }
    if (!paused) physics.step(dt, charIn, carIn);

    const st = physics.latest;
    if (st) {
      physEma += (st.stepMs - physEma) * 0.05;
      if (st.character && st.character.enabled) {
        feet.set(st.character.pos.x, st.character.pos.y, st.character.pos.z);
        vel.set(st.character.vel.x, st.character.vel.y, st.character.vel.z);
        grounded = st.character.grounded; groundMat = st.character.groundMaterial;
      }
      if (st.vehicle) {
        car.update(st.vehicle);
        if (mode === 'car') { feet.set(st.vehicle.pos.x, st.vehicle.pos.y - 0.6, st.vehicle.pos.z); vel.set(st.vehicle.linvel.x, st.vehicle.linvel.y, st.vehicle.linvel.z); }
      }
    }
    const renderCharacter = physics.characterRender.sample(now / 1000);
    if (mode === 'foot' && renderCharacter && !paused) {
      player.update(dt, new THREE.Vector3(renderCharacter.pos.x, renderCharacter.pos.y, renderCharacter.pos.z), vel, grounded, groundMat);
      if (grounded && !wasGrounded) void input.rumble(90, 0.2, 0.1);
      wasGrounded = grounded;
    }

    const focus = mode === 'foot' ? tmp.copy(player.root.position).add(new THREE.Vector3(0, 1.6, 0)) : tmp.set(feet.x, feet.y + 1.8, feet.z);
    orbit.update(dt, focus);
    // camera collision: async ray from the focus towards the camera (result applies next frame)
    if (!camRayBusy) {
      camRayBusy = true;
      const dir = orbit.backDir();
      void physics.raycast({ x: orbit.target.x, y: orbit.target.y, z: orbit.target.z }, { x: dir.x, y: dir.y, z: dir.z }, orbit.distance + 0.3).then((h) => {
        orbit.clampDist = h ? Math.max(0.8, h.toi - 0.3) : Infinity; camRayBusy = false;
      });
    }
    streamer.update(feet.x, feet.z);
    env.follow(feet);
    tiles.update(camera);
    renderer.render(scene, camera);

    const en = engineToEN(feet.x, feet.y, feet.z);
    const tot = physics.totals();
    const ct = collisionTileAt(feet.x, feet.z);
    hud.set({
      backend, fps: 1000 / frameEma, frameMs: frameEma, physMs: physEma,
      visTris: tiles.residentTris, visModels: tiles.residentModels,
      colTiles: tot.tiles, colTris: tot.tris, colMB: tot.colBytes / 1e6, colWasmMB: tot.estWasmBytes / 1e6,
      E: en.E, N: en.N, H: en.H, heading: yawToHeading(orbit.yaw), mode, speed: Math.hypot(vel.x, vel.z),
      ground: MATERIALS[groundMat]?.name ?? String(groundMat), tile: `c_${ct.i}_${ct.j}`,
    });
    const help = document.getElementById('help')!;
    help.textContent = paused ? `${input.prompt('pause')} Resume` : mode === 'foot'
      ? `${input.prompt('moveY')} move · ${input.source === 'gamepad' ? 'Ⓡ' : 'mouse'} look · ${input.prompt('sprint')} sprint · ${input.prompt('jump')} jump · ${input.prompt('interact')} enter car · ${input.prompt('cameraReset')} camera · ${input.prompt('pause')} pause`
      : `${input.prompt('throttle')} throttle · ${input.prompt('brake')} brake/reverse · ${input.prompt('steer')} steer · ${input.prompt('handbrake')} handbrake · ${input.prompt('interact')} exit · ${input.prompt('pause')} pause`;
  });
}
