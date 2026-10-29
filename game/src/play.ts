import { TyreEffects } from './vehicle/tyreEffects';
import type { PlayWorld } from './playWorld';
export type { PlayWorld } from './playWorld';
import { ParkedVehicles } from './world/parkedVehicles';
// Play session over main.ts's persistent world: input, audio, UI, actors, missions, saves and frame loop.
// Localhost hot updates dispose this session and restore it via gameplay/hotState.ts.
import * as THREE from 'three/webgpu';
import { engineToEN, enToEngine, collisionTileAt, yawToHeading, type V3 } from './core/origin';
import { devToolsEnabled } from './core/localOnly';
import { phaseTime } from './core/sun';
import { Input } from './input';
import { GameAudio } from './audio';
import { showAudioPage } from './audio/menu';
import { deadlineProgress, musicMuffle, musicScene } from './audio/music/playState';
import { GameplayAudio } from './audio/gameplay';
import { Haptics } from './input/haptics';
import { VoicePlayer } from './audio/voice';
import { ControlsHelp, controlsHelp } from './ui/controlsHelp';
import { loading } from './core/loading';
import { PlayerVisual, SPEED } from './actors/player';
import { ChaseCamera } from './vehicle/chaseCamera';
import { CarVisual, ParkedCarVisuals } from './actors/car';
import { SeatedVehicles } from './actors/seatedVehicles';
import { OrbitCamera } from './actors/camera';
import { Hud } from './ui/hud';
import { PositionHud } from './ui/positionHud';
import { StartScreen } from './ui/startScreen';
import { FullscreenPolicy, fullscreenHint } from './ui/fullscreen';
import { ShopMenu } from './ui/shopMenu';
import { IntroDialog } from './ui/introDialog';
import { RecoveryHud } from './ui/recovery';
import { PauseMenu } from './ui/menu';
import { DevMenuFlow } from './ui/devMenu';
import { FlyMode } from './gameplay/flyMode';
import { SaveStore, SLOT_IDS } from './core/save';
import { keepSavedVehicle } from './gameplay/vehicleSave';
import { SPAWN_HEIGHT, SaveGameController, driverSide, yawOf } from './gameplay/saveGame';
import { MissionSystem, OWN_VEHICLE_ID, missionSummary, ownedVehicleId } from './gameplay/missions/missions';
import { DEALER_VEHICLE_SPOT } from './gameplay/content/locations';
import { createGameplay, compassDeg } from './gameplay';
import { recoveryVehicle } from './gameplay/flippedCar';
import { SafeReset } from './gameplay/safeReset';
import { ZoneMarkers } from './gameplay/zones';
import { DropoffGuard } from './gameplay/missions/dropoffs';
import { DEFAULT_ZONE_RAY_TOP } from './gameplay/content/shops';
import { applyHot, markResume, takeResume, type HotPlay, type PlaySession } from './gameplay/hotState';
import { MATERIALS } from './shared/col/materials';
import type { CharacterInput, VehicleInput } from './physics/protocol';
import { DT } from './physics/constants';
import { holdCar, holdFoot } from './world/collisionStreaming';
import { EdgeMarker } from './world/edgeMarker';
import { preloadStarts } from './world/preloadStarts';
import { browserStorage, sessionStore } from './core/storage';
import { StreamHint } from './ui/streamHint';
import { prepareMultiplayer } from './net/preparedStart';
import type { MultiplayerClient } from './net/client';
import { SharedVehicles } from './net/sharedVehicles';
import { RemotePlayers } from './net/remotes';
import { VEHICLE_POSE_HEIGHT } from './net/height';
import { vehicleIdentity } from './net/identity';
import { Scoreboard } from './net/scoreboard';
import { PoleSession } from './gameplay/poles/session';

/** Actions ignored while a shop or the pause menu is open (Escape and D-pad down are also pause and mute). */
const LOCKED_IN_MENU = ['pause', 'mute', 'nextTrack', 'map', 'interact', 'mission', 'jump', 'sprint', 'cameraReset', 'vehicleReset', 'respawn'] as const;

/** Starts a play session; `hot` continues a hot-swapped one (dev server only). */
export async function startPlay(world: PlayWorld, hot: HotPlay | null): Promise<PlaySession> {
  const { params, backend, renderer, scene, camera, env, perf, tiles, materials, physics, rapier, manifest, streamer, coarse,
    telemetry, clock, errors, fail, hudEl, renderFrame, groundAt, spawn } = world;
  const body = new Set(document.body.children);
  const root = new THREE.Group();
  root.name = 'play-session';
  scene.add(root);
  const tyreEffects = new TyreEffects(root, params.quality);
  const edgeMarker = new EdgeMarker(physics, manifest.collision); // the visible edge of the world (world/edgeMarker.ts)
  root.add(edgeMarker.group);
  const listeners = new AbortController();
  const audio = hot?.audio ?? new GameAudio();
  const audioPreload = audio.preload();
  const fullscreen = new FullscreenPolicy();
  const positionHud = new PositionHud(camera, tiles);
  const input = new Input(renderer.domElement, () => { void audio.unlock(); }, () => fullscreen.toggle(), () => {
    if (!startScreen.active) void positionHud.copy();
  });
  const haptics = new Haptics(r => void input.rumble(r.duration, r.strong, r.weak));
  const audioGameplay = new GameplayAudio(event => {
    audio.emit(event);
    if (event.type === 'footstep') haptics.footstep(event.material);
  });
  audio.mount(document.body);
  const voice = new VoicePlayer(audio.voiceOutput());
  const voicePreload = audioPreload.then(() => voice.preload());
  addEventListener('pagehide', () => { voice.dispose(); audio.dispose(); }, { once: true, signal: listeners.signal });
  const saves = new SaveStore(browserStorage(), missionSummary);
  for (const id of SLOT_IDS) keepSavedVehicle(manifest, saves.get(id)?.lastVehicle);
  // A controlled reload after a landing (localhost dev server) continues from the autosave it just wrote.
  const resume = !hot && import.meta.hot && takeResume(sessionStore()) ? saves.get('auto') : null;
  const startScreen = new StartScreen(input, {
    hint: fullscreenHint(input.touchDevice, fullscreen.supported, fullscreen.standalone),
    onClose: () => { world.loadingScreen.enter(); fullscreen.enterScene(input.source === 'touch'); } });
  const introDialog = new IntroDialog(document.body, voice);
  if (!hot?.audio) audio.setMusicState('menu');
  const hud = new Hud(hudEl);
  const helpLearning = new ControlsHelp();
  if (hot ? hot.hudVisible !== hud.visible : !params.hud && hud.visible) hud.toggle();
  const player = new PlayerVisual(root, origin => physics.raycast(origin, { x: 0, y: -1, z: 0 }, 1.3, 'foot'));
  const car = new CarVisual(root);
  const seated = new SeatedVehicles(root, physics, groundAt);
  const parkedVisuals = new ParkedCarVisuals(root, world.prepareGraphics);
  const parkedVehicles = new ParkedVehicles(manifest, physics, parkedVisuals, seated);
  const orbit = new OrbitCamera(camera);
  const chase = new ChaseCamera(camera, physics);
  const yaw0 = spawn.yaw;
  orbit.yaw = yaw0;
  await loading.track('Player and vehicle models', Promise.all([car.load(`${import.meta.env.BASE_URL}assets/car/sedan.glb`),
    seated.load(import.meta.env.BASE_URL),
    player.load(`${import.meta.env.BASE_URL}assets/character/player.glb`, renderer), parkedVisuals.load(manifest)]));
  await Promise.all((manifest.vehicles ?? []).filter(v => v.model === 'truck').map(v => seated.addTruck(v.object_id)));
  const remotePlayers = new RemotePlayers(root, physics, { player, car, scooter: seated.scooter, truck: seated.truck, parked: parkedVisuals }, tyreEffects);
  const scoreboard = new Scoreboard(document.body);
  // Gameplay: the street router (contract Router) drives the missions; the minimap shows their HUD state.
  const { router, minimap, area, addresses, streets } = await createGameplay(manifest, document.body);
  // Draft tiles' streets join the map and the router as the drafts arrive and leave, and with the 60 s manifest poll.
  if (coarse) {
    coarse.onMapChange = () => void streets.update(coarse.roadGraphs());
    if (coarse.roadGraphs().length) coarse.onMapChange();
  }
  let multiplayerRunId = crypto.randomUUID();
  const missions = new MissionSystem({ router, addresses, onPaid: drop => {
    if (!multiplayerPlaying || devToolsEnabled(location)) return; // developer sessions stay off the scoreboard
    multiplayer?.sendDelivery({ orderId: `${multiplayerRunId}:order-${drop.orderIndex}:stop-${drop.stopIndex}`,
      addressId: drop.addressId, payout: drop.payout, tip: drop.tip, routeMeters: drop.routeMeters, job: drop.job });
  } });
  minimap.setSource(missions);
  voice.attach(missions);
  // Activation zones use a street-level check for drop-offs (gameplay/missions/dropoffs.ts).
  const zoneRay = (x: number, z: number, top: number) => physics.raycast({ x, y: top, z }, { x: 0, y: -1, z: 0 }, top + 60);
  const dropoffs = new DropoffGuard(
    (x, z) => zoneRay(x, z, DEFAULT_ZONE_RAY_TOP).then(hit => hit && { y: hit.point.y, material: hit.material }),
    (id, point) => missions.relocateDropoff(id, point));
  const zones = new ZoneMarkers(root, (target, top) => target.kind === 'dropoff' ? dropoffs.ground(target)
    : zoneRay(target.point.x, target.point.z, top).then(hit => hit?.point.y ?? null), document.body);
  zones.setSource(missions);
  const shopMenu = new ShopMenu(document.body);
  const pauseMenu = new PauseMenu(document.body, input);
  const streamHint = new StreamHint(document.body);
  const crashFlash = document.createElement('div');
  crashFlash.id = 'mp-crash-flash';
  document.body.append(crashFlash);
  let multiplayer: MultiplayerClient | null = null;
  let multiplayerPlaying = false;
  let sharedVehicles: SharedVehicles | null = null;
  const poles = new PoleSession(tiles, physics, missions, voice, document.body);
  const mapDirection = new THREE.Vector3();

  let mode: 'foot' | 'car' = 'foot';
  let drivenId: string | null = null;
  let paused = false;
  let lastCrash = 0;
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
    if (multiplayerPlaying) sharedVehicles?.release(drivenId);
    mode = 'foot';
    physics.driveVehicle(null);
    drivenId = null;
    chase.leave();
    physics.setCharacterEnabled(true, at ?? undefined);
    orbit.distance = 4.5;
  };
  /** In the vehicle `id`: a rider stays visible on the scooter and in the truck's open cab; a driver disappears into
   *  a car. */
  const enterVehicle = (id: string) => {
    fly?.set(false);
    drivenId = id;
    saveGame.usedVehicle(id);
    if (drivenId !== 'player' && !seated.of(drivenId)) parkedVisuals.takeOver(drivenId);
    mode = 'car';
    orbit.distance = 8;
  };
  // Developer tools (localhost, or ?dev=1 also on Pages): fly mode and the pause menu's Developer page (ui/devMenu.ts).
  const fly = devToolsEnabled(location) ? new FlyMode() : null;
  const devFlow = fly ? new DevMenuFlow(pauseMenu, {
    money: () => missions.money,
    addMoney: euros => missions.grantDevMoney(euros),
    flying: () => fly.active,
    setFlying: on => fly.set(on),
    built: (E, N) => { const p = enToEngine(E, N); const t = collisionTileAt(p.x, p.z); return `c_${t.i}_${t.j}` in manifest.collision; },
    teleport: ({ E, N, headingDeg }) => {
      void saveGame.teleport(E, N, headingDeg).then(ground => { if (!ground) fly.set(true); }).catch(fail);
    },
    ask: question => window.prompt(question),
    setTime: phase => { const time = phaseTime(world.clockTime(), phase); window.__render!.setTime(time.toISOString()); },
    nextTrack: () => audio.nextTrack(),
    spawnTruck: () => { void seated.parkAhead('truck', feet, camera).catch(fail); },
  }, () => saveGame.pausePage()) : null;
  // Save slots (core/save.ts), the start and pause menus (ui/menu.ts) and loading a game (gameplay/saveGame.ts).
  const saveGame = new SaveGameController({ physics, streamer, manifest, missions, recovery, saves, startScreen, pauseMenu,
    clock: world.clockTime, router,
    enterVehicle: id => { enterVehicle(id); snapChase = true; },
    spawn, car: world.car,
    view: () => ({ mode, drivenId, feet, cameraYaw: orbit.yaw }), leaveCar: () => leaveCar(null),
    placed: (at, yaw) => { feet.set(at.x, at.y, at.z); desiredVelocity.set(0, 0); orbit.yaw = yaw; },
    setPaused: value => { paused = value; }, showIntro: () => introDialog.show(), fail,
    onQuit: () => { sharedVehicles?.stop(drivenId); multiplayerPlaying = false; poles.clearClient(); remotePlayers.dispose();
      minimap.setRemotes([]); scoreboard.setClient(null); },
    scoreboardStart: () => { void scoreboard.open(startScreen, () => startScreen.show(saveGame.startFlow.start())); },
    scoreboardPause: () => { void scoreboard.open(pauseMenu, () => pauseMenu.show(saveGame.pausePage())); },
    developer: devFlow ? () => pauseMenu.show(devFlow.root()) : undefined,
    audio: () => showAudioPage(pauseMenu, audio, () => saveGame.pausePage()),
    multiplayer: () => {
      startScreen.close();
      multiplayerRunId = crypto.randomUUID();
      poles.setClient(client); scoreboard.setClient(client);
      void preparedMultiplayer.then(at => {
        if (at) { multiplayerPlaying = true; return saveGame.playAt(at); }
        startScreen.reopen(saveGame.startFlow.start());
        startScreen.element.querySelector('.start-hint')!.textContent = 'Multiplayer unavailable. Reload to reconnect.';
      }).catch(fail);
    } });
  // Joining before Ready resolves both the server-assigned spawn and a retained session's actual pose.
  const { client, ready: preparedMultiplayer } = prepareMultiplayer();
  multiplayer = client;
  sharedVehicles = new SharedVehicles(client, physics, manifest, parkedVehicles);
  const syncClock = () => world.syncClock?.(client.state.serverTime);
  client.addEventListener('welcome', syncClock);
  if (client.state.connected) syncClock();
  scoreboard.setClient(client);
  let owned = missions.vehicle;
  if (hot) {
    saveGame.restoreLastVehicle(hot.save?.lastVehicle);
    const restored = applyHot(hot, { missions, setClock: date => { world.setClock(date); }, camera: orbit });
    if (restored.drivenId) { enterVehicle(restored.drivenId); snapChase = true; }
    owned = missions.vehicle;
    if (hot.multiplayer) {
      multiplayerPlaying = true;
      poles.setClient(client);
    }
  }
  // A landing that cannot swap reloads the page (Vite full reload or a restarted dev server): autosave and resume.
  const autosaveForReload = () => { if (saveGame.autosave.save('reload')) markResume(sessionStore()); };
  import.meta.hot?.on('vite:beforeFullReload', autosaveForReload);
  import.meta.hot?.on('vite:ws:disconnect', autosaveForReload);
  const state = () => {
    const en = engineToEN(feet.x, feet.y, feet.z);
    const v = physics.latest?.vehicles.find(v => v.id === (drivenId ?? 'player')) ?? physics.latest?.vehicle;
    return { mode, paused, locomotion: player.locomotion, contacts: player.contacts, visualFeet: player.root.position.toArray(), visualVehicle: car.root.position.toArray(), cameraYaw: orbit.yaw, inputSource: input.source, feet: feet.toArray(), vel: vel.toArray(), grounded, groundMaterial: groundMat, E: en.E, N: en.N, H: en.H,
      vehicle: v ? { ...v, pos: [v.pos.x, v.pos.y, v.pos.z], modelLoaded: v.id === 'player' ? car.loaded : seated.of(v.id)?.loaded ?? true } : null,
      ...seated.snapshot(player, drivenId),
      vehicles: physics.latest?.vehicles.map(v => ({ ...v, pos: [v.pos.x, v.pos.y, v.pos.z] })) ?? [], drivenId,
      camera: { pos: camera.position.toArray(), fov: camera.fov, yaw: chase.yaw }, collision: physics.totals(), visTris: tiles.residentTris,
      visModels: tiles.residentModels, tilesSettled: !!coarse && coarse.progressAt(feet.x, feet.z).loaded === coarse.progressAt(feet.x, feet.z).total && !materials?.ground?.pending,
      frameMs: frameEma, missions: missions.snapshot(), zones: zones.snapshot(),
      recovery: recovery.snapshot(feet),
      shopMenu: { open: shopMenu.open, selected: shopMenu.selected }, fly: fly?.active ?? null,
      pauseMenu: { open: pauseMenu.open, page: pauseMenu.panel.page?.id ?? null, selected: pauseMenu.panel.selected },
      startScreen: startScreen.active, loading: saveGame.loading, autosave: saveGame.autosave.last, errors,
      multiplayer: multiplayer ? { connected: multiplayer.state.connected, nick: multiplayer.state.you?.nick,
        players: multiplayer.state.players.length, scores: multiplayer.state.scores,
        remotes: remotePlayers.markers(), remoteVisuals: remotePlayers.visualBounds(), remoteFrameMs: remotePlayers.frameMs } : null };
  };
  orbit.update(1, feet.clone().add(new THREE.Vector3(0, 1.5, 0)));
  const multiplayerAt = await preparedMultiplayer;
  const releaseStarts = await preloadStarts(world, feet, SLOT_IDS.map(id => saves.get(id)), multiplayerAt, saveGame, [audioPreload, voicePreload]);
  Object.assign(window.__game!, { input, haptics, state, physics, rapier, audio, voice, parkedVisuals, missions, router, minimap, zones, recovery,
    shopMenu, pauseMenu, saves, orbit, telemetry, coarse, streets, edgeMarker, streamer, materials, multiplayerClient: () => multiplayer, ready: true });

  if (hot?.save) startScreen.close();
  else if (resume) saveGame.hooks.play(resume);
  else startScreen.show(saveGame.startFlow.start());
  startScreen.ready(!!multiplayerAt);
  world.loadingScreen.ready(!!multiplayerAt);
  let lastDeadlineUpdate = -Infinity;
  let draftRoute: typeof minimap.routes.route = null;
  let wasCarrying = missions.phase === 'carrying';
  renderer.setAnimationLoop(() => {
    clock.frames++;
    perf.begin();
    const now = performance.now();
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    frameEma += (dt * 1000 - frameEma) * 0.05;

    input.update(dt);
    helpLearning.update(input, mode, !!fly?.active, !startScreen.active && !paused && !saveGame.loading && !shopMenu.open && !fullscreen.blocking, now);
    if (input.consume('hud')) hud.toggle();
    positionHud.handle(input, !startScreen.active);
    if (startScreen.active) {
      startScreen.update(input);
      // Let gravity settle both actors, but discard actions used to dismiss the screen.
      for (const action of Object.keys(input.actions) as (keyof typeof input.actions)[]) {
        input.consume(action); input.actions[action] = 0;
      }
      input.lookX = input.lookY = input.zoom = 0;
    }
    // An open shop menu owns the controller: world movement, camera and the other buttons wait until it closes.
    const introOpen = introDialog.open;
    if (introOpen) {
      introDialog.handle(input);
      for (const action of LOCKED_IN_MENU) input.consume(action);
      input.lookX = input.lookY = input.zoom = 0;
    }
    const menuOpen = shopMenu.open;
    const menu = menuOpen ? shopMenu.handle(input) : { choose: null, leave: false };
    if (menuOpen) {
      for (const action of LOCKED_IN_MENU) input.consume(action);
      input.lookX = input.lookY = input.zoom = 0;
    }
    // The pause menu owns the controller like a shop menu.
    if (!introOpen && saveGame.frame(input)) {
      for (const action of LOCKED_IN_MENU) input.consume(action);
      input.lookX = input.lookY = input.zoom = 0;
    }
    // Touch play runs only in fullscreen: leaving it pauses behind the overlay until the player taps back in.
    paused = fullscreen.update(input.source === 'touch' && !startScreen.active, paused);
    if (input.consume('map')) minimap.toggleExpanded();
    if (input.consume('mute')) audio.emit({ type: 'mute' });
    if (input.consume('nextTrack')) audio.nextTrack();
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
      const nearestLocal = mode === 'foot' ? physics.latest?.vehicles
        .filter(v => !multiplayerPlaying || !sharedVehicles?.record(v.id ?? null)?.occupant)
        .map(v => ({ id: v.id ?? 'player', distance: Math.hypot(v.pos.x - feet.x, v.pos.z - feet.z), y: v.pos.y }))
        .filter(v => Math.abs(v.y - feet.y) < 3).sort((a, b) => a.distance - b.distance)[0] : null;
      const nearestShared = multiplayerPlaying && mode === 'foot' ? sharedVehicles?.nearest(feet) : null;
      const nearest = nearestShared && (!nearestLocal || nearestShared.distance < nearestLocal.distance) ? nearestShared : nearestLocal;
      if (mode === 'foot' && nearest && nearest.distance < 4 && missions.mayDrive(nearest.id)) {
        const enter = (id: string) => { enterVehicle(id); physics.driveVehicle(id); physics.setCharacterEnabled(false); };
        if (multiplayerPlaying) void sharedVehicles?.acquire(nearest.id).then(id => {
          if (id && multiplayerPlaying && mode === 'foot') enter(id);
        });
        else enter(nearest.id);
      } else if (mode === 'car' && vs) {
        leaveCar(driverSide(vs));
      }
    }
    if (!paused && !lost && input.consume('vehicleReset')) {
      const target = recoveryVehicle(physics.latest?.vehicles ?? [], mode === 'car' ? drivenId : null, feet, id => missions.mayDrive(id));
      if (target && (mode === 'car' || target.id === recovery.flippedCarId)) {
        physics.resetVehicle({ x: target.pos.x, y: target.pos.y + 1.5, z: target.pos.z }, yawOf(target.rot), target.id);
      }
    }

    let charIn: CharacterInput = { vx: 0, vz: 0, jump: false };
    let carIn: VehicleInput | null = null;
    if (mode === 'foot') {
      const b = orbit.basis();
      const mx = b.fx * fwd + b.rx * side, mz = b.fz * fwd + b.rz * side;
      const jump = input.consume('jump');
      // Developer fly mode: a double tap of jump toggles it (the first tap still jumps).
      if (fly && jump && !paused) fly.tap(now / 1000);
      if (fly?.active) {
        const v = fly.velocity({ x: mx, z: mz, up: menuOpen ? 0 : input.actions.flyUp, down: menuOpen ? 0 : input.actions.flyDown,
          fast: input.sprinting });
        desiredVelocity.set(v.vx, v.vz);
        charIn = { vx: v.vx, vz: v.vz, jump: false, fly: { vy: v.vy } };
      } else {
        const speed = input.actions.walk > 0.5 ? SPEED.walk : (input.sprinting ? SPEED.sprint : SPEED.run) * missions.runFactor;
        const blend = 1 - Math.exp(-dt * (Math.hypot(mx, mz) > 0 ? 14 : 22));
        desiredVelocity.lerp(new THREE.Vector2(mx * speed, mz * speed), blend);
        charIn = { vx: desiredVelocity.x, vz: desiredVelocity.y, jump };
      }
    } else {
      desiredVelocity.set(0, 0);
      carIn = menuOpen ? { throttle: 0, steer: 0, brake: 0, handbrake: true }
        : { throttle: input.actions.throttle, steer: input.actions.steer,
          brake: input.actions.brake, handbrake: input.actions.handbrake > 0.5 };
    }
    if (carIn && vs) carIn = holdCar(carIn, vs.forwardSpeed, streamer.speedLimit(feet.x, feet.z, vel.x, vel.z)); // unloaded collision ahead
    else if (!fly?.active) charIn = holdFoot(charIn, streamer.speedLimit(feet.x, feet.z, charIn.vx, charIn.vz));
    const mission = missions.update(paused || startScreen.active ? 0 : dt, {
      position: { x: feet.x, y: feet.y, z: feet.z }, interact: !paused && input.consume('mission'), input: input.source,
      vehicleId: mode === 'car' ? drivenId ?? OWN_VEHICLE_ID : null, choose: menu.choose, leave: menu.leave,
    });
    const carrying = missions.phase === 'carrying';
    if (wasCarrying && !carrying && missions.phase === 'delivered') audio.resolveDelivery();
    else if (wasCarrying && !carrying) audio.setMusicDeadline(0);
    wasCarrying = carrying;
    audio.setMusicState(musicScene(startScreen.active, (paused || pauseMenu.open || saveGame.loading) && !introDialog.open, carrying));
    audio.setMusicMuffle(musicMuffle(introDialog.open, missions.shop !== null, mode === 'car'));
    if (carrying && now - lastDeadlineUpdate >= 1000) {
      const order = missions.snapshot();
      audio.setMusicDeadline(deadlineProgress(order.elapsed, order.par));
      lastDeadlineUpdate = now;
    }
    if (mission.checkpoint) saveGame.autosave.save(mission.checkpoint);
    shopMenu.render(missions.shop, input);
    zones.setHidden(missions.shop !== null);
    // A bought vehicle waits at the dealer's kerb: the scooter or the truck appears there, or the player's own car
    // moves there.
    // A game being loaded places its own vehicle (gameplay/saveGame.ts).
    if (saveGame.loading) owned = missions.vehicle;
    else if (missions.vehicle !== owned) {
      owned = missions.vehicle;
      const spot = DEALER_VEHICLE_SPOT;
      if ((owned === 'scooter' || owned === 'truck') && drivenId !== ownedVehicleId(owned)) void seated.park(owned, { ...spot, y: 0 });
      if (owned === 'car' && drivenId !== OWN_VEHICLE_ID) {
        void groundAt(spot.x, spot.z).then(y => physics.resetVehicle({ x: spot.x, y: y + SPAWN_HEIGHT.car, z: spot.z }, spot.yaw, OWN_VEHICLE_ID));
      }
    }
    const effectsDt = paused || startScreen.active || saveGame.loading ? 0 : dt;
    tyreEffects.begin(effectsDt);
    if (multiplayerPlaying && multiplayer && !saveGame.loading && !startScreen.active) {
      if (drivenId && multiplayer.state.connected && sharedVehicles?.record(drivenId) && !sharedVehicles.hasSeat(drivenId)) leaveCar(vs ? driverSide(vs) : feet);
      sharedVehicles?.update(now);
      remotePlayers.update(multiplayer, now, feet, effectsDt, sharedVehicles ?? undefined);
    }
    physics.updateInput(charIn, carIn, !paused && (!multiplayerPlaying || !!multiplayer?.state.connected));
    const st = physics.latest;
    if (st) poles.update(st.poles, mode === 'car' ? st.vehicle?.pos ?? feet : feet, streamer.radius);
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
        if (multiplayerPlaying && mode === 'car' && activeCar.impact && activeCar.impact.id !== lastCrash) {
          lastCrash = activeCar.impact.id;
          crashFlash.style.transition = 'none';
          crashFlash.style.opacity = String(Math.min(0.5, activeCar.impact.impulse / 12000));
          requestAnimationFrame(() => { crashFlash.style.transition = 'opacity 450ms ease-out'; crashFlash.style.opacity = '0'; });
        }
        if (mode === 'car') { feet.set(activeCar.pos.x, activeCar.pos.y - VEHICLE_POSE_HEIGHT, activeCar.pos.z); vel.set(activeCar.linvel.x, activeCar.linvel.y, activeCar.linvel.z); }
      }
    }
    if (multiplayerPlaying && !startScreen.active && !saveGame.loading) {
      const yaw = mode === 'car' && vs ? yawOf(vs.rot) - Math.PI / 2 : player.root.rotation.y - Math.PI;
      const shared = sharedVehicles?.record(drivenId);
      const poseMode = mode === 'foot' ? 'foot' : vs?.kind ?? 'car';
      const updates = sharedVehicles?.updates(now) ?? [];
      const sent = multiplayer?.sendPose({ t: now, E: feet.x, N: -feet.z, H: feet.y, yaw: Math.atan2(Math.sin(yaw), Math.cos(yaw)),
        vE: vel.x, vN: -vel.z, vH: vel.y, mode: poseMode, vehicle: poseMode === 'foot' ? null : shared?.pose.vehicle ?? vehicleIdentity(poseMode, drivenId, manifest.vehicles ?? []),
        ...(poseMode !== 'foot' && shared ? { vehicleId: shared.id } : {}),
        carrying: missions.phase === 'carrying' }, updates);
      if (sent) sharedVehicles?.sentUpdates(updates);
    }
    if (!paused && !startScreen.active && !menuOpen) {
      recovery.update(dt, { mode, drivenId, feet, vel, grounded, facing: player.root.rotation.y,
        flying: mode === 'foot' && !!fly?.active }, resetNow);
    }
    recovery.updateFlipped(paused || startScreen.active || menuOpen ? 0 : dt, mode === 'car' ? drivenId : null, feet, id => missions.mayDrive(id));
    // renderVehicle interpolates the driven vehicle (the start car while on foot); the others follow the latest step.
    const latestOf = (id: string) => (drivenId ?? 'player') === id ? renderVehicle : st?.vehicles.find(v => v.id === id) ?? null;
    const carState = latestOf('player');
    car.root.visible = !!carState;
    if (carState) car.update(carState);
    if (effectsDt > 0 && mode === 'car') {
      const vehicle = latestOf(drivenId ?? 'player');
      if (vehicle) tyreEffects.vehicle(0, vehicle.pos, vehicle.rot, vehicle.wheels, vehicle.kind ?? 'car', effectsDt, vehicle.speed);
    }
    seated.update(dt, latestOf, mode === 'car' ? drivenId : null, player);
    const renderCharacter = physics.characterRender.sample(now / 1000);
    if (mode === 'foot' && renderCharacter && !paused) {
      player.update(dt, new THREE.Vector3(renderCharacter.pos.x, renderCharacter.pos.y, renderCharacter.pos.z), vel, grounded, groundMat);
    }

    player.root.visible = !positionHud.characterHidden && (mode === 'foot' || !!seated.of(drivenId));
    const focus = mode === 'foot' ? tmp.copy(player.root.position).add(new THREE.Vector3(0, 1.25, 0)) : tmp.set(feet.x, feet.y + 1.8, feet.z);
    const chaseVehicle = drivenId && drivenId !== 'player' && !seated.of(drivenId)
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
    tyreEffects.end(camera);
    audioGameplay.update({ tick: st?.tick ?? 0, time: now / 1000, paused, mode, connected: input.connected,
      character: st?.character ?? null, vehicle: st?.vehicle ?? null, input: carIn,
      position: camera.position, forward: camera.getWorldDirection(audioForward),
      up: audioUp.set(0, 1, 0).applyQuaternion(camera.quaternion) });
    haptics.update({ now, active: !paused && !startScreen.active && !menuOpen,
      vehicle: mode === 'car' ? st?.vehicles.find(v => v.id === drivenId) ?? null : null, vehicleId: mode === 'car' ? drivenId : null,
      input: carIn, character: mode === 'foot' ? { grounded, vy: vel.y } : null });
    streamHint.update(!startScreen.active && (saveGame.loading || (!fly?.active && !streamer.supports(feet.x, feet.z))));
    const streamAt = saveGame.streamPoint(feet);
    if (!startScreen.active && !saveGame.loading) releaseStarts();
    const routeHint = missions.phase === 'carrying' ? minimap.routes.route : null;
    if (routeHint !== draftRoute) { coarse?.setRouteHint(routeHint); draftRoute = routeHint; }
    coarse?.update(streamAt.x, streamAt.z);
    parkedVehicles.update(streamAt.x, streamAt.z, drivenId, now);
    streamer.update(streamAt.x, streamAt.z, false, vel.x, vel.z);
    edgeMarker.update(() => streamer.trueEdges());
    env.follow(feet);
    positionHud.update(now, !startScreen.active);
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
    const nearEdge = recovery.render(feet, input.source === 'touch' ? 'Reset' : input.prompt('respawn'), input.prompt('vehicleReset'));
    if (multiplayerPlaying) minimap.setRemotes(remotePlayers.markers());
    minimap.update({ position: { x: feet.x, z: feet.z }, headingDeg: compassDeg(mapDirection.x, mapDirection.z), facingDeg, mode, nearEdge });
    zones.update(dt, camera, { x: feet.x, z: feet.z });
    const en = engineToEN(feet.x, feet.y, feet.z);
    const tot = physics.totals();
    const ct = collisionTileAt(feet.x, feet.z);
    hud.set({
      playerName: startScreen.active || positionHud.characterHidden ? undefined : multiplayer?.state.you?.nick, backend, fps: 1000 / frameEma, frameMs: frameEma, physMs: physEma,
      visTris: tiles.residentTris, visModels: tiles.residentModels,
      colTiles: tot.tiles, colTris: tot.tris, colMB: tot.colBytes / 1e6, colWasmMB: tot.estWasmBytes / 1e6,
      E: en.E, N: en.N, H: en.H, heading: yawToHeading(orbit.yaw), mode, speed: Math.hypot(vel.x, vel.z),
      ground: MATERIALS[groundMat]?.name ?? String(groundMat), tile: `c_${ct.i}_${ct.j}`, draft: coarse?.status,
      perf,
    });
    input.setTouchContext(startScreen.active || fullscreen.blocking ? 'hidden'
      : menuOpen || missions.shop || pauseMenu.open ? 'menu' : paused ? 'paused' : recovery.lost ? 'lost' : mode);
    const help = document.getElementById('help')!;
    help.hidden = startScreen.active || helpLearning.hidden(mode, !!fly?.active);
    help.textContent = controlsHelp(input, mode, !!fly?.active, paused, !!recovery.lost);
    world.loadingScreen.update(!startScreen.active, coarse?.status);
    perf.end();
  });

  return {
    dispose() {
      const snapshot: HotPlay = { save: startScreen.active || saveGame.loading ? null : saveGame.capture(), mode, drivenId, audio,
        camera: { yaw: orbit.yaw, pitch: orbit.pitch, distance: orbit.distance }, hudVisible: hud.visible,
        multiplayer: multiplayerPlaying };
      physics.updateInput({ vx: 0, vz: 0, jump: false }, null, false);
      renderer.setAnimationLoop(null);
      multiplayer?.disconnect();
      remotePlayers.dispose();
      tyreEffects.dispose();
      poles.dispose();
      scoreboard.dispose();
      listeners.abort();
      saveGame.dispose();
      input.dispose();
      voice.dispose();
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
