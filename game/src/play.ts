// Play session: input, audio, UI, actors, missions, saves and the frame loop on top of the world that main.ts owns
// (renderer, tiles, physics worker with the character and vehicles, collision streaming). On the localhost game (Vite
// dev server) main.ts accepts hot updates of this module and everything only it imports: `dispose()` stops the loop,
// removes the session's DOM, scene objects and listeners and returns a HotPlay (gameplay/hotState.ts), and the new
// module's `startPlay` continues from it without the start menu. Production builds have no hot path.
import * as THREE from 'three/webgpu';
import { engineToEN, collisionTileAt, yawToHeading, type V3 } from './core/origin';
import { Input } from './input';
import { GameAudio } from './audio';
import { GameplayAudio } from './audio/gameplay';
import { VoicePlayer } from './audio/voice';
import { timeControl } from './render/debug';
import { PlayerVisual, SPEED } from './actors/player';
import { ChaseCamera } from './vehicle/chaseCamera';
import { CarVisual, ParkedCarVisuals } from './actors/car';
import { ScooterVisual } from './actors/scooter';
import { OrbitCamera } from './actors/camera';
import { Hud } from './ui/hud';
import { StartScreen } from './ui/startScreen';
import { FullscreenPolicy, fullscreenHint } from './ui/fullscreen';
import { ShopMenu } from './ui/shopMenu';
import { RecoveryHud } from './ui/recovery';
import { PauseMenu } from './ui/menu';
import { SaveStore } from './core/save';
import { SaveGameController, driverSide, yawOf } from './gameplay/saveGame';
import { MissionSystem, OWN_VEHICLE_ID, SCOOTER_ID, missionSummary } from './gameplay/missions/missions';
import { INTRO_LINE_IDS, lineText } from './gameplay/content/dialogue';
import { DEALER_VEHICLE_SPOT } from './gameplay/content/locations';
import { createGameplay, compassDeg } from './gameplay';
import { SafeReset } from './gameplay/safeReset';
import { ZoneMarkers } from './gameplay/zones';
import { DropoffGuard } from './gameplay/missions/dropoffs';
import { DEFAULT_ZONE_RAY_TOP } from './gameplay/content/shops';
import { applyHot, markResume, takeResume, type HotPlay } from './gameplay/hotState';
import { MATERIALS } from './shared/col/materials';
import type { VehicleInput } from './physics/protocol';
import { DT } from './physics/constants';
import type { PhysicsClient } from './physics/client';
import type { Manifest } from './world/manifest';
import type { CollisionStreamer } from './world/collisionStreaming';
import type { CoarseTiles } from './world/coarseTiles';
import type { VisualTiles } from './world/tiles';
import type { Telemetry } from './telemetry';
import type { Environment } from './render/environment';
import type { PerformanceMonitor } from './render/performance';
import type { MaterialStyler } from './render/materials';
import type { parseParams } from './core/params';

type Pose = V3 & { yaw: number };

/** The world main.ts creates once per page and keeps across hot swaps of the session. */
export interface PlayWorld {
  params: ReturnType<typeof parseParams>;
  backend: string;
  renderer: THREE.WebGPURenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  env: Environment;
  perf: PerformanceMonitor;
  tiles: VisualTiles;
  materials: MaterialStyler | null;
  physics: PhysicsClient;
  rapier: string;
  manifest: Manifest;
  streamer: CollisionStreamer;
  coarse: CoarseTiles | null;
  telemetry: Telemetry | null;
  /** Rendered frames and simulated seconds (tests/e2e/waits.ts). */
  clock: { frames: number; simSeconds: number };
  errors: string[];
  fail: (e: unknown) => void;
  hudEl: HTMLElement;
  renderFrame: () => void;
  /** The sun clock and its setter (`window.__render.setTime`). */
  clockTime: () => Date;
  setClock: (date: Date) => void;
  /** Ground height under (x, z) from a physics ray. */
  groundAt: (x: number, z: number) => Promise<number>;
  /** Where a new game starts, and the start car's parking (both placed in physics by main.ts). */
  spawn: Pose;
  car: Pose;
}

interface PlaySession {
  /** Stops the session and returns what the next one continues from. */
  dispose(): HotPlay;
  /** Autosaves and reloads the page, which continues from the autosave (dev server only). */
  reload(): void;
}

/** Actions ignored while a shop or the pause menu is open (Escape and D-pad down are also pause and mute). */
const LOCKED_IN_MENU = ['pause', 'mute', 'map', 'interact', 'mission', 'jump', 'sprint', 'cameraReset', 'vehicleReset', 'respawn'] as const;

/** localStorage, or null where the browser blocks it (the save slots then last only for this page). */
function browserStorage(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}
function sessionStore(): Storage | null {
  try { return window.sessionStorage; } catch { return null; }
}

/** Starts a play session; `hot` continues a hot-swapped one (dev server only). */
export async function startPlay(world: PlayWorld, hot: HotPlay | null): Promise<PlaySession> {
  const { params, backend, renderer, scene, camera, env, perf, tiles, materials, physics, rapier, manifest, streamer, coarse,
    telemetry, clock, errors, fail, hudEl, renderFrame, groundAt, spawn } = world;
  const body = new Set(document.body.children);
  const root = new THREE.Group();
  root.name = 'play-session';
  scene.add(root);
  const listeners = new AbortController();
  const audio = new GameAudio();
  const audioGameplay = new GameplayAudio(event => audio.emit(event));
  const input = new Input(renderer.domElement, () => { void audio.unlock(); });
  audio.mount(document.body);
  const voice = new VoicePlayer(audio.voiceOutput());
  addEventListener('pagehide', () => { voice.dispose(); audio.dispose(); }, { once: true, signal: listeners.signal });
  const saves = new SaveStore(browserStorage(), missionSummary);
  // A controlled reload after a landing (localhost dev server) continues from the autosave it just wrote.
  const resume = !hot && import.meta.hot && takeResume(sessionStore()) ? saves.get('auto') : null;
  const intro = saves.any || hot || resume ? [] : INTRO_LINE_IDS.map(id => lineText(id));
  const fullscreen = new FullscreenPolicy();
  const startScreen = new StartScreen(input, { intro,
    hint: fullscreenHint(input.touchDevice, fullscreen.supported, fullscreen.standalone),
    onClose: () => fullscreen.enterScene(input.source === 'touch') });
  voice.narrate(intro);
  const hud = new Hud(hudEl);
  const clockControl = timeControl(world.clockTime(), date => world.setClock(date));
  if (hot ? !hot.hudVisible : !params.hud) hud.toggle();
  clockControl.element.hidden = !hud.visible;
  const player = new PlayerVisual(root, origin => physics.raycast(origin, { x: 0, y: -1, z: 0 }, 1.3, 'foot'));
  const car = new CarVisual(root);
  const scooter = new ScooterVisual(root);
  const parkedVisuals = new ParkedCarVisuals(root);
  const orbit = new OrbitCamera(camera);
  const chase = new ChaseCamera(camera, physics);
  const yaw0 = spawn.yaw;
  orbit.yaw = yaw0;
  await Promise.all([car.load(`${import.meta.env.BASE_URL}assets/car/sedan.glb`),
    scooter.load(`${import.meta.env.BASE_URL}assets/vehicles/scooter.glb`),
    player.load(`${import.meta.env.BASE_URL}assets/character/player.glb`, renderer), parkedVisuals.load(manifest)]);
  // Gameplay: the street router (contract Router) drives the missions; the minimap shows their HUD state.
  const { router, minimap, area, addresses, streets } = await createGameplay(manifest, document.body);
  // Draft tiles' streets join the map and the router as the drafts arrive and leave, and with the 60 s manifest poll.
  if (coarse) {
    coarse.onMapChange = () => void streets.update(coarse.roadGraphs());
    if (coarse.roadGraphs().length) coarse.onMapChange();
  }
  const missions = new MissionSystem({ router, addresses });
  // The scooter exists once bought (a loaded game places it in gameplay/saveGame.ts).
  const parkScooter = async (spot: V3 & { yaw: number }) => physics.spawnVehicle(
    { x: spot.x, y: await groundAt(spot.x, spot.z) + 0.55, z: spot.z }, spot.yaw, { id: SCOOTER_ID, kind: 'scooter' });
  minimap.setSource(missions);
  voice.attach(missions);
  // Activation zones at every target the HUD lists; the shop menu opens from them and locks movement. A drop-off's
  // marker waits for its street-level check, which moves a drop-off off a roof (gameplay/missions/dropoffs.ts).
  const zoneRay = (x: number, z: number, top: number) => physics.raycast({ x, y: top, z }, { x: 0, y: -1, z: 0 }, top + 60);
  const dropoffs = new DropoffGuard(
    (x, z) => zoneRay(x, z, DEFAULT_ZONE_RAY_TOP).then(hit => hit && { y: hit.point.y, material: hit.material }),
    (id, point) => missions.relocateDropoff(id, point));
  const zones = new ZoneMarkers(root, (target, top) => target.kind === 'dropoff' ? dropoffs.ground(target)
    : zoneRay(target.point.x, target.point.z, top).then(hit => hit?.point.y ?? null), document.body);
  zones.setSource(missions);
  const shopMenu = new ShopMenu(document.body);
  const pauseMenu = new PauseMenu(document.body, input);
  const mapDirection = new THREE.Vector3();

  let mode: 'foot' | 'car' = 'foot';
  let drivenId: string | null = null;
  let paused = false;
  let lastRumble = 0;
  let wasGrounded = false;
  const desiredVelocity = new THREE.Vector2();
  const feet = new THREE.Vector3(spawn.x, spawn.y, spawn.z);
  const placed = physics.latest?.character?.pos;
  if (hot && placed) feet.set(placed.x, placed.y, placed.z);
  const vel = new THREE.Vector3();
  let grounded = false, groundMat = 0;
  let frameEma = 16.7, physEma = 0;
  let last = performance.now();
  let camRayBusy = false;
  const tmp = new THREE.Vector3();
  const carQuat = new THREE.Quaternion();
  const audioForward = new THREE.Vector3(), audioUp = new THREE.Vector3();
  // Safe reset (gameplay/safeReset.ts): it arms on the player's first safe frame.
  let snapChase = false;
  const recovery = new SafeReset(area, manifest, physics, streamer, new RecoveryHud(document.body), (resetMode, heading) => {
    if (resetMode === 'car') snapChase = true;
    else { orbit.yaw = heading - Math.PI; orbit.pitch = -0.12; }
  });
  /** On foot again at `at` (null: wherever the character is placed next). */
  const leaveCar = (at: V3 | null) => {
    mode = 'foot';
    physics.driveVehicle(null);
    drivenId = null;
    chase.leave();
    physics.setCharacterEnabled(true, at ?? undefined);
    player.root.visible = true;
    orbit.distance = 4.5;
  };
  /** In the vehicle `id`: a rider stays visible on the scooter; a driver disappears into the car. */
  const enterVehicle = (id: string) => {
    drivenId = id;
    if (drivenId !== 'player' && drivenId !== SCOOTER_ID) parkedVisuals.takeOver(drivenId);
    mode = 'car';
    player.root.visible = drivenId === SCOOTER_ID;
    orbit.distance = 8;
  };
  // Save slots (core/save.ts), the start and pause menus (ui/menu.ts) and loading a game (gameplay/saveGame.ts).
  const saveGame = new SaveGameController({ physics, streamer, manifest, missions, recovery, saves, startScreen, pauseMenu,
    clockControl, defaultTime: params.time, clock: world.clockTime, setClock: world.setClock,
    spawn, car: world.car,
    view: () => ({ mode, drivenId, feet, cameraYaw: orbit.yaw }), leaveCar: () => leaveCar(null),
    placed: (at, yaw) => { feet.set(at.x, at.y, at.z); desiredVelocity.set(0, 0); orbit.yaw = yaw; },
    setPaused: value => { paused = value; }, fail });
  let owned = missions.vehicle;
  if (hot) {
    const restored = applyHot(hot, { missions, setClock: date => { world.setClock(date); clockControl.set(date); }, camera: orbit });
    if (restored.drivenId) { enterVehicle(restored.drivenId); snapChase = true; }
    owned = missions.vehicle;
  }
  // A landing that cannot swap reloads the page (Vite full reload or a restarted dev server): autosave and resume.
  const autosaveForReload = () => { if (saveGame.autosave.save('reload')) markResume(sessionStore()); };
  import.meta.hot?.on('vite:beforeFullReload', autosaveForReload);
  import.meta.hot?.on('vite:ws:disconnect', autosaveForReload);

  const state = () => {
    const en = engineToEN(feet.x, feet.y, feet.z);
    const v = physics.latest?.vehicles.find(v => v.id === (drivenId ?? 'player')) ?? physics.latest?.vehicle;
    return { mode, paused, locomotion: player.locomotion, contacts: player.contacts, visualFeet: player.root.position.toArray(), visualVehicle: car.root.position.toArray(), cameraYaw: orbit.yaw, inputSource: input.source, feet: feet.toArray(), vel: vel.toArray(), grounded, groundMaterial: groundMat, E: en.E, N: en.N, H: en.H,
      vehicle: v ? { ...v, pos: [v.pos.x, v.pos.y, v.pos.z], modelLoaded: v.id === 'player' ? car.loaded : v.id === SCOOTER_ID ? scooter.loaded : true } : null,
      scooter: { visible: scooter.root.visible, lean: scooter.leanAngle, rider: player.root.visible && player.locomotion === 'ride',
        riderPos: player.root.position.toArray(), seatPos: scooter.seat.getWorldPosition(new THREE.Vector3()).toArray() },
      vehicles: physics.latest?.vehicles.map(v => ({ ...v, pos: [v.pos.x, v.pos.y, v.pos.z] })) ?? [], drivenId,
      camera: { pos: camera.position.toArray(), fov: camera.fov, yaw: chase.yaw }, collision: physics.totals(), visTris: tiles.residentTris,
      visModels: tiles.residentModels, tilesSettled: tiles.settled && !materials?.ground?.pending,
      frameMs: frameEma, missions: missions.snapshot(), zones: zones.snapshot(),
      recovery: recovery.snapshot(feet),
      shopMenu: { open: shopMenu.open, selected: shopMenu.selected },
      pauseMenu: { open: pauseMenu.open, page: pauseMenu.panel.page?.id ?? null, selected: pauseMenu.panel.selected },
      startScreen: startScreen.active, loading: saveGame.loading, autosave: saveGame.autosave.last, errors };
  };
  Object.assign(window.__game!, { input, state, physics, rapier, audio, voice, parkedVisuals, missions, router, minimap, zones, recovery,
    shopMenu, pauseMenu, saves, orbit, telemetry, coarse, streets, ready: true });

  if (hot?.save) startScreen.close();
  else if (resume) saveGame.hooks.play(resume);
  else startScreen.show(saveGame.startFlow.start());
  startScreen.ready();
  renderer.setAnimationLoop(() => {
    clock.frames++;
    perf.begin();
    const now = performance.now();
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    frameEma += (dt * 1000 - frameEma) * 0.05;

    input.update(dt);
    if (input.consume('hud')) { hud.toggle(); clockControl.element.hidden = !hud.visible; }
    if (startScreen.active) {
      startScreen.update(input);
      // Let gravity settle both actors, but discard actions used to dismiss the screen.
      for (const action of Object.keys(input.actions) as (keyof typeof input.actions)[]) {
        input.consume(action); input.actions[action] = 0;
      }
      input.lookX = input.lookY = input.zoom = 0;
    }
    // An open shop menu owns the controller: world movement, camera and the other buttons wait until it closes.
    const menuOpen = shopMenu.open;
    const menu = menuOpen ? shopMenu.handle(input) : { choose: null, leave: false };
    if (menuOpen) {
      for (const action of LOCKED_IN_MENU) input.consume(action);
      input.lookX = input.lookY = input.zoom = 0;
    }
    // The pause menu owns the controller like a shop menu.
    if (saveGame.frame(input)) {
      for (const action of LOCKED_IN_MENU) input.consume(action);
      input.lookX = input.lookY = input.zoom = 0;
    }
    // Touch play runs only in fullscreen: leaving it pauses behind the overlay until the player taps back in.
    paused = fullscreen.update(input.source === 'touch' && !startScreen.active, paused);
    if (input.consume('map')) minimap.toggleExpanded();
    if (input.consume('mute')) audio.emit({ type: 'mute' });
    const resetCamera = !paused && input.consume('cameraReset');
    if (mode === 'foot' && !paused) {
      orbit.input(input.lookX, input.lookY, input.zoom);
      if (resetCamera) { orbit.yaw = player.root.rotation.y - Math.PI; orbit.pitch = -0.12; }
    }
    const { moveY: fwd, moveX: side } = menuOpen ? { moveY: 0, moveX: 0 } : input.actions;

    const vs = physics.latest?.vehicles.find(v => v.id === (drivenId ?? 'player')) ?? physics.latest?.vehicle;
    // Lost (gameplay/recovery.ts): Y, R or touch Reset returns to solid ground instead of entering or leaving a car.
    const lost = recovery.lost !== null;
    const respawn = input.consume('respawn');
    const resetNow = lost && !paused && (input.consume('vehicleReset') || respawn);
    if (lost) input.consume('interact');
    if (!paused && input.consume('interact')) {
      const nearest = mode === 'foot' ? physics.latest?.vehicles
        .map(v => ({ vehicle: v, distance: Math.hypot(v.pos.x - feet.x, v.pos.z - feet.z) }))
        .filter(v => Math.abs(v.vehicle.pos.y - feet.y) < 3)
        .sort((a, b) => a.distance - b.distance)[0] : null;
      if (mode === 'foot' && nearest && nearest.distance < 4 && missions.mayDrive(nearest.vehicle.id ?? OWN_VEHICLE_ID)) {
        enterVehicle(nearest.vehicle.id ?? 'player');
        physics.driveVehicle(drivenId);
        physics.setCharacterEnabled(false);
      } else if (mode === 'car' && vs) {
        leaveCar(driverSide(vs));
      }
    }
    if (mode === 'car' && !paused && !lost && input.consume('vehicleReset') && vs) {
      physics.resetVehicle({ x: vs.pos.x, y: vs.pos.y + 1.5, z: vs.pos.z }, yawOf(vs.rot), drivenId ?? 'player');
    }

    let charIn = { vx: 0, vz: 0, jump: false };
    let carIn: VehicleInput | null = null;
    if (mode === 'foot') {
      const b = orbit.basis();
      const mx = b.fx * fwd + b.rx * side, mz = b.fz * fwd + b.rz * side;
      const speed = input.actions.walk > 0.5 ? SPEED.walk : (input.sprinting ? SPEED.sprint : SPEED.run) * missions.runFactor;
      const blend = 1 - Math.exp(-dt * (Math.hypot(mx, mz) > 0 ? 14 : 22));
      desiredVelocity.lerp(new THREE.Vector2(mx * speed, mz * speed), blend);
      charIn = { vx: desiredVelocity.x, vz: desiredVelocity.y, jump: input.consume('jump') };
    } else {
      desiredVelocity.set(0, 0);
      carIn = menuOpen ? { throttle: 0, steer: 0, brake: 0, handbrake: true }
        : { throttle: input.actions.throttle, steer: input.actions.steer,
          brake: input.actions.brake, handbrake: input.actions.handbrake > 0.5 };
    }
    const mission = missions.update(paused || startScreen.active ? 0 : dt, {
      position: { x: feet.x, z: feet.z }, interact: !paused && input.consume('mission'), input: input.source,
      vehicleId: mode === 'car' ? drivenId ?? OWN_VEHICLE_ID : null, choose: menu.choose, leave: menu.leave,
    });
    if (mission.checkpoint) saveGame.autosave.save(mission.checkpoint);
    shopMenu.render(missions.shop, input);
    zones.setHidden(missions.shop !== null);
    // A bought vehicle waits at the dealer's kerb: the scooter appears there, or the player's own car moves there.
    // A game being loaded places its own vehicle (gameplay/saveGame.ts).
    if (saveGame.loading) owned = missions.vehicle;
    else if (missions.vehicle !== owned) {
      owned = missions.vehicle;
      const spot = DEALER_VEHICLE_SPOT;
      if (owned === 'scooter' && drivenId !== SCOOTER_ID) void parkScooter({ ...spot, y: 0 });
      if (owned === 'car' && drivenId !== OWN_VEHICLE_ID) {
        void groundAt(spot.x, spot.z).then(y => physics.resetVehicle({ x: spot.x, y: y + 0.75, z: spot.z }, spot.yaw, OWN_VEHICLE_ID));
      }
    }
    if (!paused) physics.step(dt, charIn, carIn);

    const st = physics.latest;
    clock.simSeconds = (st?.tick ?? 0) * DT;
    const renderVehicle = physics.vehicleRender.sample(now / 1000);
    if (st) {
      physEma += (st.stepMs - physEma) * 0.05;
      if (st.character && st.character.enabled) {
        feet.set(st.character.pos.x, st.character.pos.y, st.character.pos.z);
        vel.set(st.character.vel.x, st.character.vel.y, st.character.vel.z);
        grounded = st.character.grounded; groundMat = st.character.groundMaterial;
      }
      parkedVisuals.update(st.vehicles);
      const activeCar = st.vehicles.find(v => v.id === drivenId);
      if (activeCar) {
        const strike = activeCar.curbStrike;
        if (strike && strike.id !== lastRumble) {
          lastRumble = strike.id;
          if (mode === 'car') void input.rumble(120, strike.rumble, strike.rumble * 0.6);
        }
        if (mode === 'car') { feet.set(activeCar.pos.x, activeCar.pos.y - 0.6, activeCar.pos.z); vel.set(activeCar.linvel.x, activeCar.linvel.y, activeCar.linvel.z); }
      }
    }
    if (!paused && !startScreen.active && !menuOpen) {
      recovery.update(dt, { mode, drivenId, feet, vel, grounded, facing: player.root.rotation.y }, resetNow);
    }
    // renderVehicle interpolates the driven vehicle (the start car while on foot); the others follow the latest step.
    const latestOf = (id: string) => (drivenId ?? 'player') === id ? renderVehicle : st?.vehicles.find(v => v.id === id) ?? null;
    const carState = latestOf('player'), scooterState = latestOf(SCOOTER_ID);
    if (carState) car.update(carState);
    if (scooterState) scooter.update(dt, scooterState);
    if (mode === 'car' && drivenId === SCOOTER_ID && scooterState) player.ride(dt, scooter.seat);
    const renderCharacter = physics.characterRender.sample(now / 1000);
    if (mode === 'foot' && renderCharacter && !paused) {
      player.update(dt, new THREE.Vector3(renderCharacter.pos.x, renderCharacter.pos.y, renderCharacter.pos.z), vel, grounded, groundMat);
      if (grounded && !wasGrounded) void input.rumble(90, 0.2, 0.1);
      wasGrounded = grounded;
    }

    const focus = mode === 'foot' ? tmp.copy(player.root.position).add(new THREE.Vector3(0, 1.25, 0)) : tmp.set(feet.x, feet.y + 1.8, feet.z);
    const chaseVehicle = drivenId && drivenId !== 'player' && drivenId !== SCOOTER_ID
      ? physics.latest?.vehicles.find(v => v.id === drivenId) : renderVehicle;
    if (mode === 'car' && chaseVehicle && !paused) {
      chase.update(dt, chaseVehicle, input.lookX, input.lookY, resetCamera || snapChase);
      snapChase = false;
    }
    else if (mode === 'foot') orbit.update(dt, focus);
    // camera collision: async ray from the focus towards the camera (result applies next frame)
    if (mode === 'foot' && !camRayBusy) {
      camRayBusy = true;
      const dir = orbit.backDir();
      void physics.raycast({ x: orbit.target.x, y: orbit.target.y, z: orbit.target.z }, { x: dir.x, y: dir.y, z: dir.z }, orbit.distance + 0.3).then((h) => {
        orbit.clampDist = h ? Math.max(0.8, h.toi - 0.3) : Infinity; camRayBusy = false;
      });
    }
    audioGameplay.update({ tick: st?.tick ?? 0, time: now / 1000, paused, mode, connected: input.connected,
      character: st?.character ?? null, vehicle: st?.vehicle ?? null, input: carIn,
      position: camera.position, forward: camera.getWorldDirection(audioForward),
      up: audioUp.set(0, 1, 0).applyQuaternion(camera.quaternion) });
    const streamAt = saveGame.streamPoint(feet);
    coarse?.update(streamAt.x, streamAt.z);
    streamer.update(streamAt.x, streamAt.z);
    env.follow(feet);
    tiles.update(camera);
    renderFrame();
    window.__render?.afterFrame?.();
    if (!startScreen.active && !paused) {
      telemetry?.tick(now, camera, () => ({ x: feet.x, y: feet.y, z: feet.z, speed: Math.hypot(vel.x, vel.z),
        vehicle: mode === 'car' ? drivenId ?? 'player' : null }));
    }

    router.mode = mode;
    const facing = mode === 'car' && vs ? mapDirection.set(1, 0, 0).applyQuaternion(carQuat.set(vs.rot.x, vs.rot.y, vs.rot.z, vs.rot.w))
      : mapDirection.set(Math.sin(player.root.rotation.y), 0, Math.cos(player.root.rotation.y));
    const facingDeg = compassDeg(facing.x, facing.z);
    camera.getWorldDirection(mapDirection);
    const nearEdge = recovery.render(feet, input.source === 'touch' ? 'Reset' : input.prompt('respawn'));
    minimap.update({ position: { x: feet.x, z: feet.z }, headingDeg: compassDeg(mapDirection.x, mapDirection.z), facingDeg, mode, nearEdge });
    zones.update(dt, camera, { x: feet.x, z: feet.z });

    const en = engineToEN(feet.x, feet.y, feet.z);
    const tot = physics.totals();
    const ct = collisionTileAt(feet.x, feet.z);
    hud.set({
      backend, fps: 1000 / frameEma, frameMs: frameEma, physMs: physEma,
      visTris: tiles.residentTris, visModels: tiles.residentModels,
      colTiles: tot.tiles, colTris: tot.tris, colMB: tot.colBytes / 1e6, colWasmMB: tot.estWasmBytes / 1e6,
      E: en.E, N: en.N, H: en.H, heading: yawToHeading(orbit.yaw), mode, speed: Math.hypot(vel.x, vel.z),
      ground: MATERIALS[groundMat]?.name ?? String(groundMat), tile: `c_${ct.i}_${ct.j}`, draft: coarse?.status,
      perf,
    });
    input.setTouchContext(startScreen.active || fullscreen.blocking ? 'hidden'
      : menuOpen || missions.shop || pauseMenu.open ? 'menu' : paused ? 'paused' : recovery.lost ? 'lost' : mode);
    const help = document.getElementById('help')!;
    help.textContent = paused ? `${input.prompt('pause')} Resume`
      : recovery.lost ? `${input.prompt('respawn')} return to solid ground · ${input.prompt('map')} map · ${input.prompt('pause')} pause`
      : mode === 'foot'
      ? `${input.prompt('moveY')} move · ${input.source === 'gamepad' ? 'Ⓡ' : 'mouse'} look · ${input.prompt('sprint')} sprint · ${input.prompt('jump')} jump · ${input.prompt('interact')} enter vehicle · ${input.prompt('cameraReset')} camera · ${input.prompt('map')} map · ${input.prompt('pause')} pause`
      : `${input.prompt('throttle')} throttle · ${input.prompt('brake')} brake/reverse · ${input.prompt('steer')} steer · ${input.prompt('handbrake')} handbrake · ${input.prompt('interact')} exit · ${input.prompt('cameraReset')} camera · ${input.prompt('map')} map · ${input.prompt('pause')} pause`;
    perf.end();
  });

  return {
    dispose() {
      const snapshot: HotPlay = { save: startScreen.active || saveGame.loading ? null : saveGame.capture(), mode, drivenId,
        camera: { yaw: orbit.yaw, pitch: orbit.pitch, distance: orbit.distance }, hudVisible: hud.visible };
      renderer.setAnimationLoop(null);
      listeners.abort();
      saveGame.dispose();
      input.dispose();
      voice.dispose();
      audio.dispose();
      minimap.dispose();
      if (coarse) coarse.onMapChange = null;
      fullscreen.dispose();
      root.removeFromParent();
      root.traverse(o => {
        const mesh = o as THREE.Mesh;
        if (!mesh.isMesh) return;
        mesh.geometry.dispose();
        for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) m.dispose();
      });
      for (const el of [...document.body.children]) if (!body.has(el)) el.remove();
      return snapshot;
    },
    reload() {
      autosaveForReload();
      location.reload();
    },
  };
}

