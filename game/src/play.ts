import { captureAllowed, captureControl } from './telemetry/capture';
import { optional } from './core/retryFetch';
import { RoundUI } from './net/rounds';
import { multiplayerStart } from './gameplay/startPoints';
import { bindRoundReset } from './net/roundReset';
import { VehicleSignals } from './vehicle/signals';
import { VehicleBeams } from './vehicle/beams';
import { TyreEffects } from './vehicle/tyreEffects';
import { mechanicsOf } from './vehicle/mechanics';
import type { PlayWorld } from './playWorld';
export type { PlayWorld } from './playWorld';
import { ParkedVehicles } from './world/parkedVehicles';
import { TrafficClient } from './traffic/client';
import { TRAFFIC_CAPS } from './traffic/constants';
import { TRAFFIC_VOICES } from './audio/mix';
import { parkedNight } from './world/parkedOccupancy';
// Play session over main.ts's persistent world: input, audio, UI, actors, missions, saves and frame loop.
// Localhost hot updates dispose this session and restore it via gameplay/hotState.ts.
import * as THREE from 'three/webgpu';
import { engineToEN, enToEngine, collisionTileAt, yawToHeading, type V3 } from './core/origin';
import { devToolsEnabled } from './core/localOnly';
import { carDamageConsole } from './dev/carDamage';
import { cycleHour } from './core/dayCycle';
import { Input, type Action } from './input';
import { GameAudio } from './audio';
import { showAudioPage } from './audio/menu';
import { deadlineProgress, musicMuffle, musicScene } from './audio/music/playState';
import { GameplayAudio } from './audio/gameplay';
import { Haptics } from './input/haptics';
import { VoicePlayer } from './audio/voice';
import { ControlsHelp } from './ui/controlsHelp';
import { loading } from './core/loading';
import { PlayerVisual, SPEED } from './actors/player';
import { ChaseCamera } from './vehicle/chaseCamera';
import { CarVisual, ParkedCarVisuals } from './actors/car';
import { SeatedVehicles } from './actors/seatedVehicles';
import { OrbitCamera } from './actors/camera';
import { Hud } from './ui/hud';
import { DevLayers } from './ui/devLayers';
import { PositionHud } from './ui/positionHud';
import { StartScreen } from './ui/startScreen';
import { FullscreenPolicy, fullscreenHint } from './ui/fullscreen';
import { ShopMenu } from './ui/shopMenu';
import { IntroDialog } from './ui/introDialog';
import { RecoveryHud } from './ui/recovery';
import { PauseMenu } from './ui/menu';
import { DevMenuFlow } from './ui/devMenu';
import { TimePanel } from './ui/timePanel';
import { FlyMode } from './gameplay/flyMode';
import { ObjectEditor } from './dev/editor';
import { SaveStore, SLOT_IDS } from './core/save';
import { keepSavedVehicle } from './gameplay/vehicleSave';
import { SPAWN_HEIGHT, SaveGameController, driverSide, yawOf } from './gameplay/saveGame';
import { clearProgress } from './gameplay/progressReset';
import { MissionSystem, OWN_VEHICLE_ID, SCOOTER_ID, TRUCK_ID, missionSummary, ownedVehicleId } from './gameplay/missions/missions';
import { DEALER_VEHICLE_SPOT, HEIST_DROP, HEIST_TRUCK_SPOT, PIZZERIA_VEHICLE_SPOT } from './gameplay/content/locations';
import { lineText } from './gameplay/content/dialogue';
import { bringSpots, kerbSpots } from './gameplay/kerbSpots';
import { vehicleGround } from './gameplay/vehicleSave';
import { createGameplay, compassDeg } from './gameplay';
import { autopilotParam, createAutopilot, type GoToOptions } from './gameplay/autopilot';
import { DriveMeThere } from './gameplay/missions/driveMeThere';
import { recoveryVehicle } from './gameplay/flippedCar';
import { SafeReset } from './gameplay/safeReset';
import { ZoneMarkers } from './gameplay/zones';
import { DropoffGuard } from './gameplay/missions/dropoffs';
import { DEFAULT_ZONE_RAY_TOP } from './gameplay/content/shops';
import { disposePlayScene, applyHot, markResume, takeResume, type HotPlay, type PlaySession } from './gameplay/hotState';
import { MATERIALS } from './shared/col/materials';
import type { CharacterInput, VehicleInput } from './physics/protocol';
import { DT, VEHICLES, type VehicleKind } from './physics/constants';
import { RearSteerControl } from './vehicle/rearSteerControl';
import { RearSteerTag } from './ui/rearSteerTag';
import { DamageHud } from './ui/damageHud';
import { drvOf, type Drv } from './net/drv';
import { EdgeMarker } from './world/edgeMarker';
import { preloadStarts } from './world/preloadStarts';
import { browserStorage, sessionStore } from './core/storage';
import { StreamHint } from './ui/streamHint';
import { prepareMultiplayer } from './net/preparedStart';
import type { MultiplayerClient } from './net/client';
import { SharedVehicles } from './net/sharedVehicles';
import { MultiplayerProgress } from './net/progress';
import { RemotePlayers } from './net/remotes';
import { VEHICLE_POSE_HEIGHT } from './net/height';
import { vehicleIdentity } from './net/identity';
import { Scoreboard } from './net/scoreboard';
import { PoleSession } from './gameplay/poles/session';
import { FramePacer } from './render/framePacer';
import { QUALITY } from './render/quality';
import { Walkers, walkerCap, type WalkerThreat } from './world/walkers';
import { chassisForward } from './traffic/pedestrians';
import { CROWD_RADIUS } from './audio/mix';
import { viewClearance } from './render/viewClearance';
import { CITIES, CITY, HOME_CITY, cityPageUrl, isHome, type CityName } from './core/city';
import { PortalTriggers, TURKU_PORTALS, chassisYaw, exitPose, portalsIn, returnTarget, type PortalDef } from './gameplay/portals';
import { PortalMarkers } from './gameplay/portalMarkers';
import { CityAvailability, cameThrough, showTransition, takeTicket, walletOnly, writeTicket, type TravelTicket } from './gameplay/travel';

/** "Bring my vehicle here" looks for a kerb within this many metres of the player, off screen first, then up to
 *  BRING_FAR_M nearest first, ground-checking at most BRING_CHECKS candidates. */
const BRING_NEAR_SEARCH_M = 40, BRING_FAR_M = 150, BRING_CHECKS = 24;
/** Milliseconds between attempts to place the heist's trophy truck in its yard. */
const HEIST_RETRY_MS = 2000;
/** The truck heist's reward car parks at a road-graph kerb spot this far from the drop-off (first within the near
 *  radius, else the far one), out of the drop zone and at the drop-off's road level, not on the bridge above. */
const HEIST_CAR_MIN_M = 10, HEIST_CAR_NEAR_M = 30, HEIST_CAR_FAR_M = 60, HEIST_CAR_LEVEL_M = 1.5;
const rideKind = (id: string): VehicleKind => (id === SCOOTER_ID ? 'scooter' : id === TRUCK_ID ? 'truck' : 'car');

/** Actions ignored while a shop or the pause menu is open (Escape and D-pad down are also pause and mute). */
/** ?extrapolate=0: the player's car and character never extrapolate past the newest physics state (the A/B of
 *  actors/interpolation.ts lateSteps; remove with it once the late-snapshot extrapolation is confirmed). */
const RENDER_EXTRAPOLATION = new URLSearchParams(globalThis.location?.search ?? '').get('extrapolate') !== '0';
const LOCKED_IN_MENU = ['pause', 'mute', 'nextTrack', 'map', 'interact', 'mission', 'jump', 'sprint', 'cameraReset', 'vehicleReset', 'respawn'] as const;

/** Starts a play session; `hot` continues a hot-swapped one (dev server only). */
/** `kept`: what a city switch carries from the session that ended (main.ts travel): the audio graph and the open
 *  multiplayer socket, which moves to this city's room. */
export async function startPlay(world: PlayWorld, hot: HotPlay | null, kept?: { audio?: GameAudio; socket?: WebSocket | null }): Promise<PlaySession> {
  const { params, backend, renderer, scene, camera, env, perf, tiles, materials, physics, rapier, manifest, streamer, coarse,
    telemetry, clock, errors, fail, hudEl, renderFrame, groundAt, spawn } = world;
  const body = new Set(document.body.children);
  const root = new THREE.Group();
  root.name = 'play-session';
  scene.add(root);
  const vehicleSignals = new VehicleSignals();
  const rearControl = new RearSteerControl();
  const rearTag = new RearSteerTag(document.body, VEHICLES.truck.rearSteer?.maxAngle ?? 1);
  const damageHud = new DamageHud(document.body);
  const vehicleBeams = new VehicleBeams(root, physics);
  const tyreEffects = new TyreEffects(root, params.quality);
  const edgeMarker = new EdgeMarker(physics, manifest.collision); // the visible edge of the world (world/edgeMarker.ts)
  root.add(edgeMarker.group);
  const listeners = new AbortController();
  const audio = hot?.audio ?? kept?.audio ?? new GameAudio();
  const audioPreload = audio.preload();
  const fullscreen = new FullscreenPolicy();
  const devLayers = new DevLayers(scene, coarse, renderer, radius => { env.setScreenshotDistance(camera, radius); env.setCityDistance(camera, 15000); }, camera);
  // The crosshair target comes from a collision ray (Rapier in the worker); only building meshes at its hit are raycast.
  const positionHud = new PositionHud(camera, tiles, undefined, () => devLayers.filterRevision,
    (origin, direction, far) => physics.raycast(origin, direction, far, 'solid'));
  const input = new Input(renderer.domElement, () => { void audio.unlock(); }, () => fullscreen.toggle(), () => {
    if (!startScreen.active) void positionHud.copy();
  });
  // Panel buttons name both the key and the pad button, in the names of the pad seen last.
  input.watchPrompts(() => {
    const both = (action: Action) => `${input.prompt(action, 'keyboard')} / ${input.prompt(action, input.padFamily)}`;
    audio.setMuteHint(both('mute'));
    devLayers.setToggleHint(both('layerPanel'));
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
  const multiplayerProgress = new MultiplayerProgress(sessionStore());
  for (const id of SLOT_IDS) keepSavedVehicle(manifest, saves.get(id)?.lastVehicle);
  // A controlled reload after a landing (localhost dev server) continues from the autosave it just wrote.
  const resume = !hot && import.meta.hot && takeResume(sessionStore()) ? saves.get('auto') : null;
  const startScreen = new StartScreen(input, {
    hint: fullscreenHint(input.touchDevice, fullscreen.supported, fullscreen.standalone),
    onClose: () => { world.loadingScreen.enter(); fullscreen.enterScene(input.source === 'touch'); } });
  const introDialog = new IntroDialog(document.body, voice);
  if (!hot?.audio) audio.setMusicState('menu');
  const hud = new Hud(hudEl);
  const helpLearning = new ControlsHelp(document.getElementById('help')!);
  if (hot ? hot.hudVisible !== hud.visible : !params.hud && hud.visible) hud.toggle();
  const player = new PlayerVisual(root, origin => physics.raycast(origin, { x: 0, y: -1, z: 0 }, 1.3, 'foot'));
  const car = new CarVisual(root);
  const seated = new SeatedVehicles(root, physics, groundAt, world.prepareGraphics);
  const parkedVisuals = new ParkedCarVisuals(root, world.prepareGraphics);
  const damageConsole = carDamageConsole(physics, car, devToolsEnabled(location), root, parkedVisuals);
  const parkedVehicles = new ParkedVehicles(manifest, physics, parkedVisuals, seated, QUALITY[params.quality].parkedBodies);
  // Ambient traffic shares the parked batches, so it reserves their instances before the first load.
  const traffic = new TrafficClient(physics, parkedVisuals, parkedVehicles, manifest, params.traffic ?? TRAFFIC_CAPS[params.quality],
    params.trafficDensity, world.trafficClock, root);
  // Developer object editor (localhost; dev/editor/README.md): visual previews and edit sessions, no simulation change.
  const editor = new ObjectEditor({ scene, camera, renderer, tiles, vegetation: world.vegetation, lights: world.lights, physics, coarse,
    signals: () => traffic.signals,
    device: () => ({ backend, gamepad: input.gamepadName, playstation: input.padFamily === 'playstation', viewport: [innerWidth, innerHeight], pixelRatio: devicePixelRatio }),
    // Editing is per player: each browser keeps its own session; in multiplayer the session names the player.
    player: () => { const you = multiplayerPlaying ? multiplayer?.state.you : null; return you ? { id: you.id, nick: you.nick } : null; } });
  Object.assign(window.__game!, { editor });
  traffic.attach(coarse);
  // Ambient walkers: shared clock + resident FOOT network; the camera ring only picks which to draw (world/walkers).
  const walkers = new Walkers(root, { cap: walkerCap(params.quality, params.walkers), now: world.sharedNow,
    birthHour: utc => multiplayerPlaying ? cycleHour(utc).hour : world.dayClock.hourAt(utc),
    raycast: (origin, dir, maxToi, filter) => physics.raycast(origin, dir, maxToi, filter), graphs: () => coarse?.roadGraphs() ?? [],
    threats: () => walkerThreats, trafficTime: () => traffic.clockTime() });
  // Players the walkers react to this frame (world/walkers/reactions.ts): the local character or driven vehicle, and
  // remote players. Filled after the physics state is read.
  const walkerThreats: WalkerThreat[] = [];
  const orbit = new OrbitCamera(camera);
  const chase = new ChaseCamera(camera, physics);
  const yaw0 = spawn.yaw;
  orbit.yaw = yaw0;
  // The player character is required; vehicles, the parked fleet and walkers are optional (core/retryFetch.ts).
  await loading.track('Player and vehicle models', Promise.all([optional('player car', car.load(), undefined),
    optional('scooter and truck', seated.load(import.meta.env.BASE_URL, renderer), undefined),
    player.load(`${import.meta.env.BASE_URL}assets/character/player.glb`, renderer), optional('parked cars', parkedVisuals.load(manifest), undefined),
    optional('walkers', walkers.load(`${import.meta.env.BASE_URL}assets/walkers/walkers.bin`), undefined)]));
  await Promise.all((manifest.vehicles ?? []).filter(v => v.model === 'truck').map(v => optional('truck', seated.addTruck(v.object_id), undefined)));
  let remoteEngines = new Set<string>();
  const remotePlayers = new RemotePlayers(root, physics, { player, scooter: seated.scooter, truck: seated.truck, parked: parkedVisuals }, tyreEffects);
  const scoreboard = new Scoreboard(document.body);
  const { router, minimap, area, addresses, streets } = await createGameplay(manifest, document.body);
  streets.connectDrafts(coarse);
  let multiplayerRunId = crypto.randomUUID();
  const missions = new MissionSystem({ router, addresses, onPaid: drop => {
    if (!multiplayerPlaying || devToolsEnabled(location)) return; // developer sessions stay off the scoreboard
    multiplayer?.sendDelivery({ orderId: `${multiplayerRunId}:order-${drop.orderIndex}:stop-${drop.stopIndex}`,
      addressId: drop.addressId, payout: drop.payout, tip: drop.tip, routeMeters: drop.routeMeters, job: drop.job });
  } });
  // The missions, shops and story are Turku content (core/city.ts): elsewhere their state waits, the wallet shows.
  const storyCity = isHome();
  minimap.setSource(storyCity ? missions : walletOnly(missions));
  voice.attach(missions);
  const zoneRay = (x: number, z: number, top: number) => physics.raycast({ x, y: top, z }, { x: 0, y: -1, z: 0 }, top + 60);
  const dropoffs = new DropoffGuard(
    (x, z) => zoneRay(x, z, DEFAULT_ZONE_RAY_TOP).then(hit => hit && { y: hit.point.y, material: hit.material }),
    (id, point) => missions.relocateDropoff(id, point));
  const zones = new ZoneMarkers(root, (target, top) => target.kind === 'dropoff' ? dropoffs.ground(target)
    : zoneRay(target.point.x, target.point.z, top).then(hit => hit?.point.y ?? null), document.body);
  if (storyCity) zones.setSource(missions);
  const shopMenu = new ShopMenu(document.body);
  const pauseMenu = new PauseMenu(document.body, input);
  const streamHint = new StreamHint(document.body);
  const crashFlash = document.createElement('div');
  crashFlash.id = 'mp-crash-flash';
  document.body.append(crashFlash);
  let multiplayer: MultiplayerClient | null = null;
  let multiplayerPlaying = false;
  let sharedVehicles: SharedVehicles | null = null;
  /** The `drv` pose record of the driven 4WS truck (SPEC §5): throttle and rear mode from physics, rpm and gear from
   *  the local sound drivetrain; undefined for every other vehicle or on foot. */
  const drivenDrv = (id: string | null): Drv | undefined => {
    const drive = audioGameplay.truckDrive;
    return id && id === drivenId && drive ? drvOf(physics.latest?.vehicles.find(v => v.id === id), drive.rpm, drive.gear) : undefined;
  };
  const poles = new PoleSession(tiles, physics, missions, voice, document.body);
  // City portals (gameplay/portals.ts): arches under the Turku bridges, the return arch in the other cities.
  const portalList = portalsIn();
  /** Ground under (x, z) from a downward ray at `top` (null: the sky), or null while its collision is not resident. */
  const groundBelow = async (x: number, z: number, top: number | null) => {
    const t = collisionTileAt(x, z);
    if (!physics.loaded.has(`c_${t.i}_${t.j}`)) return null;
    return (await physics.raycast({ x, y: top ?? 500, z }, { x: 0, y: -1, z: 0 }, top === null ? 1000 : 20))?.point.y ?? null;
  };
  const portalMarkers = new PortalMarkers(root, portalList, groundBelow, document.body);
  const portalTriggers = new PortalTriggers(portalList);
  const availability = new CityAvailability();
  let travelling = false;
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
  const clearanceBody = new THREE.Vector3();
  const carQuat = new THREE.Quaternion();
  const audioForward = new THREE.Vector3(), audioUp = new THREE.Vector3();
  let snapChase = false;
  const recovery = new SafeReset(area, manifest, physics, streamer, new RecoveryHud(document.body), (resetMode, heading) => {
    if (resetMode === 'car') snapChase = true;
    else { orbit.yaw = heading - Math.PI; orbit.pitch = -0.12; }
  });
  /** On foot again at `at` (null: wherever the character is placed next). */
  const leaveCar = (at: V3 | null) => {
    const leaving = physics.latest?.vehicles.find(v => v.id === drivenId);
    if (leaving) leaving.horn = false;
    if (multiplayerPlaying) sharedVehicles?.release(drivenId);
    mode = 'foot';
    physics.driveVehicle(null);
    drivenId = null;
    chase.leave();
    physics.setCharacterEnabled(true, at ?? undefined);
    orbit.distance = 4.5;
  };
  /** Enter `id`; riders remain visible on the scooter and in the truck's open cab. */
  const enterVehicle = (id: string) => {
    fly?.set(false);
    drivenId = id;
    vehicleSignals.enter();
    rearControl.sync(physics.latest?.vehicles.find(v => v.id === id)?.rearMode);
    if (multiplayerPlaying) sharedVehicles?.drive(id);
    saveGame.usedVehicle(id);
    if (drivenId !== 'player' && !seated.of(drivenId)) parkedVisuals.takeOver(drivenId, manifest.vehicles?.find(v => v.object_id === id));
    mode = 'car';
    orbit.distance = 8;
  };
  // Developer tools (localhost, or ?dev=1 also on Pages): fly mode, the time panel (T) and the Developer page (ui/devMenu.ts).
  const fly = devToolsEnabled(location) ? new FlyMode() : null;
  const timePanel = fly ? new TimePanel(document.body, world.dayClock, world.weather) : null;
  const devFlow = fly && timePanel ? new DevMenuFlow(pauseMenu, {
    money: () => missions.money,
    addMoney: euros => missions.grantDevMoney(euros),
    flying: () => fly.active,
    setFlying: on => fly.set(on),
    built: (E, N) => { const p = enToEngine(E, N); const t = collisionTileAt(p.x, p.z); return `c_${t.i}_${t.j}` in manifest.collision; },
    teleport: ({ E, N, headingDeg }) => {
      void saveGame.teleport(E, N, headingDeg).then(ground => { if (!ground) fly.set(true); }).catch(fail);
    },
    ask: question => window.prompt(question),
    clock: world.dayClock, timePanel: () => timePanel.open, toggleTimePanel: () => timePanel.toggle(),
    nextTrack: () => audio.nextTrack(),
    spawnTruck: () => { void seated.parkAhead('truck', feet, camera).catch(fail); },
    naming: () => positionHud.naming, setNaming: on => positionHud.setNaming(on),
  }, () => saveGame.pausePage()) : null;
  /** Parks the player's vehicle `id` at `spot`, settled onto the ground `y` (default: the ground under the spot, seen
   *  from the sky): the car is reset there, the scooter and the truck are spawned (or moved) there. */
  const parkRide = async (id: string, spot: { x: number; z: number; yaw: number }, y?: number) => {
    const kind = rideKind(id);
    const at = { x: spot.x, y: (y ?? await groundAt(spot.x, spot.z)) + SPAWN_HEIGHT[kind], z: spot.z };
    if (id === OWN_VEHICLE_ID) physics.resetVehicle(at, spot.yaw, id);
    else physics.spawnVehicle(at, spot.yaw, { id, kind });
  };
  /** In multiplayer another player holds the vehicle (its local body is gone): it cannot be fetched. */
  const heldElsewhere = (id: string) => multiplayerPlaying && !physics.latest?.vehicles.some(v => v.id === id)
    && !!sharedVehicles?.record(id);
  /** Pause menu "Bring my vehicle here": the nearest ground-checked kerb by the player, off screen when one is near. */
  const bringHere = async () => {
    const id = missions.rideId, kind = rideKind(id);
    if (heldElsewhere(id)) { missions.bringFailed(); return; }
    const forward = camera.getWorldDirection(new THREE.Vector3());
    const halfFov = Math.atan(Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) * camera.aspect);
    const spots = kerbSpots(router.graph, feet, VEHICLES[kind].halfExt.z, BRING_FAR_M);
    const close = spots.filter(s => Math.hypot(s.x - feet.x, s.z - feet.z) <= BRING_NEAR_SEARCH_M);
    const candidates = [...bringSpots(close, feet, { camera: camera.position, forward, halfFov }),
      ...spots.slice(close.length)].slice(0, BRING_CHECKS);
    for (const spot of candidates) {
      const y = await vehicleGround(physics, spot, { kind }, SPAWN_HEIGHT[kind], true);
      if (y === null) continue;
      await parkRide(id, spot, y);
      missions.broughtHere();
      return;
    }
    missions.bringFailed();
  };
  /**
   * "Reset progress" (SaveGameController.resetProgress, gameplay/progressReset.ts), after the player left any vehicle:
   * every city's stored progress goes; in a room the shared records resync; the room resets the player (score, seat,
   * own vehicles); the bought scooter and truck go. The new game starts at Turku's default start: on this page, or
   * after a trip there (from another city, or from Turku entered at a portal exit, ?spawn=) that carries the fresh
   * mission and the multiplayer choice like a trip home.
   */
  const forgetProgress = async (): Promise<boolean> => {
    autopilot.cancel('progress reset'); fly?.set(false);
    saves.clear(); clearProgress(browserStorage(), sessionStore());
    missions.restore(null);
    // The new game's start car is a fresh one: its body damage goes with the progress.
    physics.setVehicleDamage(OWN_VEHICLE_ID, null);
    if (multiplayerPlaying) { sharedVehicles?.resetRound(); multiplayerRunId = crypto.randomUUID(); }
    // The room forgets this tab's player also when it is only connected (solo after a multiplayer game); offline,
    // the next hello carries the reset.
    await multiplayer?.resetProgress();
    for (const id of [SCOOTER_ID, TRUCK_ID]) physics.removeVehicle(id);
    if (storyCity && !params.spawnGiven) return true;
    travelling = true;
    writeTicket(sessionStore(), { v: 1, from: CITY.name, to: HOME_CITY, portal: null, ride: null, mission: missions.serialize(),
      multiplayer: multiplayerPlaying, at: Date.now() });
    showTransition(document.body, CITIES[HOME_CITY]);
    setTimeout(() => { if (world.travel && !storyCity) void world.travel(HOME_CITY); else location.assign(cityPageUrl(HOME_CITY, location)); }, 700);
    return false;
  };
  const saveGame = new SaveGameController({ physics, streamer, manifest, missions, recovery, saves, startScreen, pauseMenu,
    tileResolved: coarse ? (x, z) => coarse.resolvedAt(x, z) : undefined,
    clock: world.clockTime, router,
    bringVehicle: {
      reason: () => {
        const reason = missions.bringHereReason();
        return reason === 'reason_bring_cooldown' ? `Ready again in ${Math.ceil(missions.bringCooldownLeft)} s`
          : reason && lineText(reason);
      },
      run: () => { void bringHere().catch(fail); },
    },
    enterVehicle: id => { enterVehicle(id); snapChase = true; },
    spawn, car: world.car,
    view: () => ({ mode, drivenId, feet, cameraYaw: orbit.yaw }), leaveCar: () => leaveCar(null),
    placed: (at, yaw) => { feet.set(at.x, at.y, at.z); desiredVelocity.set(0, 0); orbit.yaw = yaw; },
    setPaused: value => { paused = value; }, showIntro: () => introDialog.show(), fail,
    onAutosave: mission => { if (multiplayerPlaying) multiplayerProgress.save(multiplayer?.session ?? null, mission); },
    onQuit: () => { sharedVehicles?.stop(drivenId); multiplayerPlaying = false; poles.clearClient(); remotePlayers.dispose();
      minimap.setRemotes([]); scoreboard.setClient(null); },
    returnHome: storyCity ? undefined : {
      detail: `To the ${returnTarget(CITY.name, cameThrough(sessionStore())).place.split(',')[0]} portal`,
      run: () => depart(HOME_CITY, null) },
    resetProgress: { forget: forgetProgress,
      at: () => multiplayerPlaying && multiplayer?.state.you ? multiplayerStart(multiplayer.state.you, undefined, undefined, roomStart) : null },
    scoreboardStart: () => { void scoreboard.open(startScreen, () => startScreen.show(saveGame.startFlow.start())); },
    scoreboardPause: () => { void scoreboard.open(pauseMenu, () => pauseMenu.show(saveGame.pausePage())); },
    developer: devFlow ? () => pauseMenu.show(devFlow.root()) : undefined,
    timedRounds: () => rounds.open(), multiplayerActive: () => multiplayerPlaying,
    audio: () => showAudioPage(pauseMenu, audio, () => saveGame.pausePage()),
    multiplayer: () => {
      startScreen.close();
      multiplayerRunId = crypto.randomUUID();
      poles.setClient(client); scoreboard.setClient(client);
      void preparedMultiplayer.then(at => {
        // A late welcome can point outside the solo preload ring; let safe placement stream that area.
        if (at) { releaseStarts(); multiplayerPlaying = true; return saveGame.playAt(multiplayerStart(client.state.you!, client.state.players.find(p => p.id === client.state.you?.id), undefined, roomStart), () => sharedVehicles!.restore(), multiplayerProgress.load(client.session)); }
        startScreen.reopen(saveGame.startFlow.start());
        startScreen.showNotice('Multiplayer unavailable. Reload to reconnect.');
      }).catch(fail);
    } });
  // The room hands out Turku's spawn slots around the office: a fresh join elsewhere (another city, or ?spawn=, which
  // also carries a portal exit) starts at this page's own ground-checked start.
  const roomStart = isHome() && !params.spawnGiven ? undefined : world.spawn;
  const ticket = hot ? null : takeTicket(sessionStore(), CITY.name, Date.now());
  const multiplayerStartState = prepareMultiplayer(roomStart, kept?.socket, !!ticket);
  const { client, ready: preparedMultiplayer } = multiplayerStartState;
  multiplayer = client;
  sharedVehicles = new SharedVehicles(client, physics, manifest, parkedVehicles);
  sharedVehicles.drvOf = drivenDrv;
  const rounds = new RoundUI(client, pauseMenu, () => pauseMenu.show(saveGame.pausePage()),
    () => saveGame.hooks.resume(), () => { paused = true; }, () => multiplayerPlaying, () => input.prompt('pause'));
  bindRoundReset(client, () => multiplayerPlaying, sharedVehicles, physics, saveGame, () => {
    multiplayerProgress.clear(client.session);
    multiplayerRunId = crypto.randomUUID(); leaveCar(null); fly?.set(false); remotePlayers.dispose();
  }, fail, roomStart);
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
      city: CITY.name, portals: portalMarkers.snapshot(), travelling,
      pauseMenu: { open: pauseMenu.open, page: pauseMenu.panel.page?.id ?? null, selected: pauseMenu.panel.selected },
      startScreen: startScreen.active, loading: saveGame.loading, autosave: saveGame.autosave.last, errors,
      multiplayer: multiplayer ? { connected: multiplayer.state.connected, nick: multiplayer.state.you?.nick,
        rounds: multiplayer.state.rounds, players: multiplayer.state.players.length, scores: multiplayer.state.scores,
        remotes: remotePlayers.markers(), remoteVisuals: remotePlayers.visualBounds(), remoteFrameMs: remotePlayers.frameMs } : null };
  };
  orbit.update(1, feet.clone().add(new THREE.Vector3(0, 1.5, 0)));
  // The network handshake runs alongside world loading; a slow/offline room must not delay solo play.
  // The autopilot (gameplay/autopilot): drives this player through the normal input path, so physics, collisions
  // and the multiplayer pose sync see a player; missions, the dev console and ?autopilot= start trips.
  const autopilot = createAutopilot({
    mode: () => mode,
    vehicle: () => physics.latest?.vehicles.find(v => v.id === (drivenId ?? 'player')) ?? physics.latest?.vehicle ?? null,
    feet: () => ({ x: feet.x, y: feet.y, z: feet.z }),
    cameraBasis: () => orbit.basis(),
    footRoute: (a, b) => router.route(a, b, 'foot'),
    carRoute: (a, b) => router.route(a, b, 'car'),
  }, coarse);
  input.driver = (actions, dt) => autopilot.control(actions, dt);
  const driveMeThere = new DriveMeThere(missions, autopilot);
  // ?autopilot=target drives to the active mission target, ?autopilot=x,z[,walk|drive][,kmh] to a point, once
  // the game runs and the ground under the player is resident.
  let autopilotStart: (() => void) | null = (() => {
    const raw = new URLSearchParams(location.search).get('autopilot');
    if (raw === 'target') return () => { driveMeThere.start(); };
    const at = autopilotParam();
    return at ? () => { autopilot.goTo({ x: at.x, z: at.z }, { ...(at.mode ? { mode: at.mode } : {}), ...(at.speed ? { speed: at.speed } : {}) }); } : null;
  })();
  const releaseStarts = await preloadStarts(world, feet, SLOT_IDS.map(id => saves.get(id)), multiplayerStartState.position ?? null, saveGame, [audioPreload, voicePreload], [player.root, car.root]);
  Object.assign(window.__game!, { damage: damageConsole, vehicleSignals, vehicleBeams, enterVehicle: (id: string) => { enterVehicle(id); physics.driveVehicle(id); physics.setCharacterEnabled(false); }, input, haptics, state, physics, rapier, audio, voice, parkedVisuals, traffic, walkers, missions, router, minimap, zones, recovery, autopilot, driveMeThere: (options?: GoToOptions) => driveMeThere.start(options),
    shopMenu, pauseMenu, saves, orbit, telemetry, coarse, devLayers, portals: portalMarkers, city: CITY.name, timePanel, streets, edgeMarker, streamer, materials, multiplayerClient: () => multiplayer, viewClearance, ready: true });

  // Tests and the trip bench leave through a portal as its trigger does (the ticket, the ride, the money).
  window.__game!.depart = (to: CityName, portalId?: string) => depart(to, portalList.find(p => p.id === portalId) ?? null);
  window.__game!.replay = { snapshot: () => ({ epochMs: world.dayClock.sharedNow(), seconds: 0, seed: 20261102,
    population: { traffic: traffic.snapshot(), walkers: walkers.snapshot() } }) };
  const capture = captureAllowed() ? captureControl(window.__game!, document.body) : null;
  if (capture) { pauseMenu.capture = capture; window.__game!.capture = capture; }

  /** Leaves for `to` (gameplay/travel.ts): the ticket carries the mission save, the ride and the multiplayer choice. */
  const depart = (to: CityName, portal: PortalDef | null) => {
    if (travelling) return;
    travelling = true;
    const driven = mode === 'car' ? physics.latest?.vehicles.find(v => v.id === drivenId) : undefined;
    const home = to === HOME_CITY ? returnTarget(CITY.name, cameThrough(sessionStore())) : null;
    // The shared car stays in this city's room, in the portal, free for anyone: the ride continues as the own vehicle.
    if (multiplayerPlaying) sharedVehicles?.release(drivenId);
    if (!multiplayerPlaying) saveGame.autosave.save('travel');
    writeTicket(sessionStore(), { v: 1, from: CITY.name, to, portal: home?.id ?? portal?.id ?? null,
      ride: driven && drivenId ? { id: drivenId, kind: driven.kind ?? 'car', ...(driven.damage ? { damage: driven.damage } : {}) } : null,
      mission: missions.serialize(),
      multiplayer: multiplayerPlaying, at: Date.now() });
    showTransition(document.body, CITIES[to]);
    paused = true;
    const spawn = home ? exitPose(home) : undefined;
    // In place when the page can switch cities (one renderer, physics world and connection); a page load otherwise.
    setTimeout(() => { if (world.travel) void world.travel(to, spawn); else location.assign(cityPageUrl(to, location, spawn)); }, 700);
  };
  /** The vehicle the player drove through in, parked at the arrival (Turku: on the portal's lanes past the deck; elsewhere
   *  the start parking by the centre), ready to be entered; null when it cannot be placed. */
  const seatRide = async (ride: NonNullable<TravelTicket['ride']>, home: PortalDef | null): Promise<string | null> => {
    const id = [OWN_VEHICLE_ID, SCOOTER_ID, TRUCK_ID].includes(ride.id) ? ride.id : OWN_VEHICLE_ID;
    const exit = home ? enToEngine(exitPose(home).E, exitPose(home).N) : null;
    const spot = exit && home ? { x: exit.x, z: exit.z, yaw: chassisYaw(home.headingDeg) } : world.car;
    const y = exit && home ? await groundBelow(spot.x, spot.z, home.rayTop) : world.car.y;
    if (y === null) return null;
    await parkRide(id, spot, y);
    // The start car's dents travel with it (a page-load arrival spawned it fresh).
    if (id === OWN_VEHICLE_ID && ride.id === OWN_VEHICLE_ID && ride.damage) physics.setVehicleDamage(id, ride.damage);
    const t0 = performance.now();
    while (!physics.latest?.vehicles.some(v => v.id === id && Math.hypot(v.pos.x - spot.x, v.pos.z - spot.z) < 1)) {
      if (performance.now() - t0 > 10000) return null;
      physics.step(1 / 60, { vx: 0, vz: 0, jump: false }, null);
      await new Promise(r => setTimeout(r, 20));
    }
    return id;
  };
  /** Arrival from another city: no start menu; solo or the target's multiplayer room, as the player left. */
  const arrive = (t: TravelTicket) => {
    startScreen.close();
    const home = TURKU_PORTALS.find(p => p.id === t.portal && isHome()) ?? null;
    // world.spawn is the city centre, or in Turku the ?spawn= exit pose the leaving page asked for.
    const at = { x: spawn.x, y: spawn.y, z: spawn.z, yaw: spawn.yaw };
    const seat = t.ride ? () => seatRide(t.ride!, home) : undefined;
    const solo = () => saveGame.playAt(at, seat, t.mission);
    if (!t.multiplayer) { void solo().catch(fail); return; }
    multiplayerRunId = crypto.randomUUID();
    poles.setClient(client); scoreboard.setClient(client);
    void preparedMultiplayer.then(joined => {
      if (!joined) {
        poles.clearClient(); scoreboard.setClient(null);
        portalMarkers.say(`Multiplayer is unavailable in ${CITY.title}: playing solo.`, performance.now(), 6);
        return solo();
      }
      releaseStarts(); multiplayerPlaying = true;
      // The room freed this player's seats here on arrival (hello `arrival`); a seat it still reports is the trip's
      // ride's to replace (SharedVehicles.drive releases it).
      return saveGame.playAt(at, async () => {
        const kept = await sharedVehicles!.restore();
        return (seat ? await seat() : null) ?? kept;
      }, t.mission);
    }).catch(fail);
  };
  if (hot?.save) startScreen.afterBrowserWarning(() => startScreen.close());
  else if (ticket) startScreen.afterBrowserWarning(() => arrive(ticket));
  else if (resume) startScreen.afterBrowserWarning(() => saveGame.hooks.play(resume));
  else startScreen.show(saveGame.startFlow.start());
  const multiplayerReady = multiplayerStartState.position === undefined ? null : !!multiplayerStartState.position;
  startScreen.ready(multiplayerReady);
  world.loadingScreen.ready(multiplayerReady);
  void preparedMultiplayer.then(at => {
    if (listeners.signal.aborted) return;
    startScreen.setMultiplayerReady(!!at);
    world.loadingScreen.ready(!!at);
  });
  let lastDeadlineUpdate = -Infinity;
  let lastCrowd = -1;
  let draftRoute: typeof minimap.routes.route = null;
  let wasCarrying = missions.phase === 'carrying';
  // The truck heist's trophy truck (TRUCK_ID; offered only to a player who owns no truck): placed in the yard once its
  // collision cell is resident, taken away when the job ends. After a game is loaded it is placed again.
  let heistTruck = false, heistRetryAt = 0, wasLoading = saveGame.loading;
  const heistTile = collisionTileAt(HEIST_TRUCK_SPOT.x, HEIST_TRUCK_SPOT.z);
  const placeHeistTruck = async () => {
    const y = await vehicleGround(physics, HEIST_TRUCK_SPOT, { kind: 'truck' }, SPAWN_HEIGHT.truck, false);
    if (y === null) return false;
    await parkRide(TRUCK_ID, HEIST_TRUCK_SPOT, y);
    return true;
  };
  /** The reward car: the start car (the player's own car when they bought it) at the nearest ground-checked kerb. */
  const parkHeistCar = async () => {
    const drop = HEIST_DROP.point;
    for (const radius of [HEIST_CAR_NEAR_M, HEIST_CAR_FAR_M]) {
      const spots = kerbSpots(router.graph, drop, VEHICLES.car.halfExt.z, radius)
        .filter(spot => Math.hypot(spot.x - drop.x, spot.z - drop.z) >= HEIST_CAR_MIN_M);
      for (const spot of spots.slice(0, BRING_CHECKS)) {
        const y = await vehicleGround(physics, { ...spot, y: drop.y! }, { kind: 'car' }, SPAWN_HEIGHT.car, false);
        if (y === null || Math.abs(y - drop.y!) > HEIST_CAR_LEVEL_M) continue;
        await parkRide(OWN_VEHICLE_ID, spot, y);
        return;
      }
    }
    console.warn('[missions] truck heist: no kerb spot for the reward car near the drop-off');
  };
  const framePacer = new FramePacer(params.mobile ? 30 : 0);
  Object.assign(window.__game!, { framePacer });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      vehicleSignals.update(false, false, false); audio.emit({ type: 'horns', vehicles: [] });
      physics.updateInput({ vx: 0, vz: 0, jump: false }, null, false);
    }
  }, { signal: listeners.signal });
  renderer.setAnimationLoop(() => {
    if (window.__render?.beforeFrame?.() === false) return;
    const now = performance.now();
    if (document.hidden || !framePacer.ready(now)) return;
    physics.beginFrame(now);
    clock.frames++;
    perf.begin();
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    frameEma += (dt * 1000 - frameEma) * 0.05;

    devLayers.restore();
    input.update(dt);
    helpLearning.update(input, mode, !!fly?.active, !startScreen.active && !paused && !saveGame.loading && !shopMenu.open && !fullscreen.blocking, now);
    if (input.consume('hud')) hud.toggle();
    positionHud.handle(input, !startScreen.active);
    devLayers.handle(input, !startScreen.active);
    timePanel?.handle(input, !startScreen.active);
    editor.handle(input, !startScreen.active && !paused && !shopMenu.open && !introDialog.open && !saveGame.loading, now);
    if (editor.modal) {
      for (const action of LOCKED_IN_MENU) input.consume(action);
      input.lookX = input.lookY = input.zoom = 0;
    }
    if (startScreen.active) {
      startScreen.update(input);
      // Let gravity settle both actors, but discard actions used to dismiss the screen.
      for (const action of Object.keys(input.actions) as (keyof typeof input.actions)[]) {
        input.consume(action); input.actions[action] = 0;
      }
      input.lookX = input.lookY = input.zoom = 0;
    }
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
    if (!introOpen && !rounds.blocking && saveGame.frame(input)) {
      for (const action of LOCKED_IN_MENU) input.consume(action);
      input.lookX = input.lookY = input.zoom = 0;
    }
    paused = rounds.pauseState(paused, pauseMenu.open || saveGame.loading);
    if (rounds.blocking) for (const action of LOCKED_IN_MENU) input.consume(action);
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
      // A moving ambient car is claimed first (it becomes an ordinary vehicle), then entered like any other.
      const moving = mode === 'foot' ? traffic.nearest(feet) : null;
      const enter = (id: string) => { enterVehicle(id); physics.driveVehicle(id); physics.setCharacterEnabled(false); };
      const take = (id: string) => {
        if (!multiplayerPlaying) { enter(id); return; }
        if (sharedVehicles?.entering) return;
        void sharedVehicles?.acquire(id).then(acquired => {
          if (!multiplayerPlaying || mode !== 'foot') return;
          if (acquired) enter(acquired);
          else portalMarkers.say('Car not available', performance.now(), 3);
        });
      };
      if (mode === 'foot' && moving && (!nearest || moving.distance < nearest.distance) && missions.mayDrive('traffic')) {
        void traffic.enter(moving.handle).then(id => { if (id && mode === 'foot') take(id); });
      } else if (mode === 'foot' && nearest && nearest.distance < 4 && missions.mayDrive(nearest.id)) {
        take(nearest.id);
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

    vehicleSignals.update(mode === 'car' && !paused && !menuOpen && !introOpen && !rounds.blocking && !startScreen.active, input.consume('lights'), input.actions.horn > .5);
    // The worker freezes while paused; transmit the live release even without another physics step.
    if (mode === 'car' && vs) { vs.lights = vehicleSignals.lights; vs.horn = vehicleSignals.horn; }
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
    if (carIn) { carIn.lights = vehicleSignals.lights; carIn.horn = vehicleSignals.horn; }
    // A 4WS vehicle (the trophy truck): a sprint press (A, Shift) cycles the held rear mode; the autopilot drives in
    // AUTO. A press of A that is also `mission` at a shop or drop-off opens or hands over there instead.
    const fourWheelSteer = mode === 'car' && !!VEHICLES[vs?.kind ?? 'car'].rearSteer;
    if (carIn && fourWheelSteer) {
      const press = !paused && !menuOpen && input.consume('sprint');
      const rearMode = rearControl.update(press && !(storyCity && input.actions.mission > 0.5 && missions.interactable));
      carIn.rearMode = autopilot.active ? 'auto' : rearMode;
    }
    rearTag.update(fourWheelSteer && !startScreen.active ? vs : null, input.source === 'touch' ? '' : input.prompt('sprint'), now);
    damageHud.update(mode === 'car' && !startScreen.active ? vs?.damage : null, now);
    const ride = physics.latest?.vehicles.find(v => v.id === missions.rideId);
    const mission = storyCity ? missions.update(paused || startScreen.active ? 0 : dt, {
      position: { x: feet.x, y: feet.y, z: feet.z }, interact: !paused && input.consume('mission'), button: input.prompt('mission'),
      vehicleId: mode === 'car' ? drivenId ?? OWN_VEHICLE_ID : null, choose: menu.choose, leave: menu.leave,
      rideAt: ride ? { x: ride.pos.x, y: ride.pos.y, z: ride.pos.z } : null,
    }) : { bought: null, opened: false, checkpoint: null, park: false, heist: null };
    if (mission.park && !heldElsewhere(missions.rideId)) void parkRide(missions.rideId, PIZZERIA_VEHICLE_SPOT).catch(fail);
    if (mission.heist === 'delivered') void parkHeistCar().catch(fail);
    const heistWanted = storyCity && missions.heist !== null && missions.vehicle !== 'truck';
    if (wasLoading && !saveGame.loading && heistTruck && missions.vehicle !== 'truck') {
      physics.removeVehicle(TRUCK_ID); heistTruck = false; // a loaded game places its own
    }
    wasLoading = saveGame.loading;
    if (heistWanted && !heistTruck && !saveGame.loading && now >= heistRetryAt && physics.loaded.has(`c_${heistTile.i}_${heistTile.j}`)) {
      heistRetryAt = now + HEIST_RETRY_MS;
      void placeHeistTruck().then(placed => {
        if (!placed) return;
        if (missions.heist !== null) heistTruck = true;
        else physics.removeVehicle(TRUCK_ID); // the job ended while the truck was being placed
      }).catch(fail);
    }
    if (!heistWanted && heistTruck && missions.vehicle !== 'truck') {
      if (drivenId === TRUCK_ID) leaveCar(null);
      physics.removeVehicle(TRUCK_ID);
      heistTruck = false;
    }
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
      const id = ownedVehicleId(owned);
      if (id && drivenId !== id) void parkRide(id, DEALER_VEHICLE_SPOT).catch(fail);
    }
    const effectsDt = paused || startScreen.active || saveGame.loading ? 0 : dt;
    tyreEffects.begin(effectsDt);
    traffic.setMultiplayer(multiplayerPlaying && multiplayer ? { offsetMs: multiplayer.serverNow() - (performance.timeOrigin + performance.now()),
      agreed: multiplayer.state.roads, sources: multiplayer.state.vehicleSources, watch: multiplayer.state.watch } : null);
    if (multiplayerPlaying && multiplayer?.state.connected) multiplayer.reportRoads(traffic.local, traffic.revision);
    if (multiplayerPlaying && multiplayer && !saveGame.loading && !startScreen.active) {
      if (!rounds.blocking) sharedVehicles?.update(now); const seatExit = sharedVehicles?.seatExit(now);
      if (drivenId && seatExit) leaveCar(seatExit);
    }
    const simulating = !paused && (!multiplayerPlaying || !!multiplayer?.state.connected);
    const extrapolating = simulating && RENDER_EXTRAPOLATION;
    physics.updateInput(charIn, carIn, simulating);
    const st = physics.latest;
    if (st) poles.update(st.poles, mode === 'car' ? st.vehicle?.pos ?? feet : feet, streamer.radius);
    clock.simSeconds = (st?.tick ?? 0) * DT;
    const renderVehicle = physics.vehicleRender.sample(now / 1000, extrapolating);
    if (renderVehicle && mode === 'car') { renderVehicle.lights = vehicleSignals.lights; renderVehicle.horn = vehicleSignals.horn; }
    // A shared vehicle this player owns is drawn by the remote-player renderer from its local body: the driven one at
    // the same render pose as everything else (the chase camera follows it), never the newer raw worker state.
    if (multiplayerPlaying && multiplayer && !saveGame.loading && !startScreen.active) {
      remotePlayers.update(multiplayer, now, feet, effectsDt, sharedVehicles ?? undefined,
        id => renderVehicle && id === drivenId && renderVehicle.id === id ? renderVehicle : null);
    }
    if (st) {
      physEma += (st.stepMs - physEma) * 0.05;
      if (st.character && st.character.enabled) {
        feet.set(st.character.pos.x, st.character.pos.y, st.character.pos.z);
        vel.set(st.character.vel.x, st.character.vel.y, st.character.vel.z);
        grounded = st.character.grounded; groundMat = st.character.groundMaterial;
      }
      traffic.update(now, camera, st);
      traffic.updatePedestrians(now, () => walkers.trafficObstacles());
      audio.emit({ type: 'traffic', ...traffic.audible(camera.position, TRAFFIC_VOICES) });
      parkedVisuals.setView(camera, world.dayClock.night);
      // The driven car is drawn at its render pose, which the chase camera follows; the others at the latest step.
      const drivenRender = renderVehicle && drivenId && renderVehicle.id === drivenId ? renderVehicle : null;
      parkedVisuals.update(drivenRender ? st.vehicles.map(v => v.id === drivenId ? { ...v, pos: drivenRender.pos, rot: drivenRender.rot, wheels: drivenRender.wheels } : v)
        : st.vehicles, parkedVehicles);
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
      walkerThreats.length = 0;
      const half = mode === 'car' && activeCar ? VEHICLES[activeCar.kind ?? 'car'].halfExt : null;
      walkerThreats.push({ x: feet.x, y: feet.y, z: feet.z, vx: vel.x, vz: vel.z,
        car: half && activeCar ? { ...chassisForward(activeCar.rot), length: 2 * half.x, width: 2 * half.z } : null });
      if (multiplayerPlaying) walkerThreats.push(...remotePlayers.threats);
    }
    if (multiplayerPlaying && !startScreen.active && !saveGame.loading) {
      const yaw = mode === 'car' && vs ? yawOf(vs.rot) - Math.PI / 2 : player.root.rotation.y - Math.PI;
      const shared = sharedVehicles?.record(drivenId);
      const poseMode = mode === 'foot' ? 'foot' : vs?.kind ?? 'car';
      const updates = sharedVehicles?.updates(now) ?? [];
      const sent = multiplayer?.sendPose({ t: now, E: feet.x, N: -feet.z, H: feet.y, yaw: Math.atan2(Math.sin(yaw), Math.cos(yaw)),
        vE: vel.x, vN: -vel.z, vH: vel.y, mode: poseMode, vehicle: poseMode === 'foot' ? null : shared?.pose.vehicle ?? vehicleIdentity(poseMode, drivenId, manifest.vehicles ?? []),
        ...(poseMode !== 'foot' && shared && sharedVehicles?.hasSeat(drivenId!) ? { vehicleId: shared.id } : {}),
        lights: mode === 'car' ? vs?.lights ?? 1 : 0, horn: mode === 'car' && vehicleSignals.horn,
        ...(mode === 'car' && drivenDrv(drivenId) ? { drv: drivenDrv(drivenId) } : {}),
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
      if (vehicle) {
        tyreEffects.vehicle(0, vehicle.pos, vehicle.rot, vehicle.wheels, vehicle.kind ?? 'car', effectsDt, vehicle.speed);
        const bay = mechanicsOf(vehicle.damage);
        tyreEffects.engineBay(0, vehicle.pos, vehicle.rot, bay.steam, bay.smoke, effectsDt);
      }
    }
    seated.update(dt, latestOf, mode === 'car' ? drivenId : null, player);
    const renderCharacter = physics.characterRender.sample(now / 1000, extrapolating);
    if (mode === 'foot' && renderCharacter && !paused) {
      player.update(dt, new THREE.Vector3(renderCharacter.pos.x, renderCharacter.pos.y, renderCharacter.pos.z), vel, grounded, groundMat);
    }

    player.root.visible = !positionHud.characterHidden && (mode === 'foot' || !!seated.of(drivenId));
    const focus = mode === 'foot' ? tmp.copy(player.root.position).add(new THREE.Vector3(0, 1.25, 0)) : tmp.set(feet.x, feet.y + 1.8, feet.z);
    // The camera follows the drawn pose of the driven vehicle, never a newer raw physics state.
    const chaseVehicle = renderVehicle && (renderVehicle.id ?? 'player') === (drivenId ?? 'player') ? renderVehicle
      : st?.vehicles.find(v => v.id === drivenId) ?? null;
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
    vehicleBeams.update(mode === 'car' ? latestOf(drivenId ?? 'player') : null, now);
    audio.emit({ type: 'horns', vehicles: [
      ...(vehicleSignals.horn && vs ? [{ id: 'local', kind: vs.kind ?? 'car' as const, position: vs.pos }] : []),
      ...(multiplayerPlaying ? remotePlayers.horns : []),
      ...traffic.horns(camera.position),
    ] });
    // Remote trucks' engine voices from their synced wheels and `drv` (net/remotes.ts); gone ones are released.
    const engineIds = new Set<string>();
    if (multiplayerPlaying) for (const e of remotePlayers.engines) { engineIds.add(e.id); audio.engineVoice(e.id, e.state); }
    for (const id of remoteEngines) if (!engineIds.has(id)) audio.engineVoice(id, null);
    remoteEngines = engineIds;
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
    if (autopilotStart && !startScreen.active && !saveGame.loading && !paused
      && (!coarse || coarse.progressAt(feet.x, feet.z).loaded === coarse.progressAt(feet.x, feet.z).total)) { autopilotStart(); autopilotStart = null; }
    const routeHint = autopilot.hint ?? (missions.phase === 'carrying' ? minimap.routes.route : null);
    if (routeHint !== draftRoute) { coarse?.setRouteHint(routeHint); draftRoute = routeHint; }
    coarse?.update(streamAt.x, streamAt.z, now, saveGame.loading ? undefined : vel, true, camera.position.y);
    parkedVehicles.setNight(parkedNight(world.sharedNow(), multiplayerPlaying ? undefined : utc => world.dayClock.hourAt(utc)), world.sharedNow());
    parkedVehicles.update(streamAt.x, streamAt.z, drivenId, now);
    streamer.update(streamAt.x, streamAt.z, false, vel.x, vel.z);
    edgeMarker.update(() => streamer.trueEdges());
    env.follow(feet);
    walkers.update(dt, camera, now);
    const crowd = walkers.nearby(CROWD_RADIUS);
    if (crowd !== lastCrowd) { lastCrowd = crowd; audio.emit({ type: 'crowd', walkers: crowd }); }
    editor.update(now);
    positionHud.update(now, !startScreen.active);
    devLayers.update();
    // Vegetation between the camera and the player (or the road ahead) fades; none in fly mode or on the start screen.
    const clearing = startScreen.active || fly?.active || !(mode === 'car' || player.root.visible) ? null
      : { body: mode === 'foot' ? clearanceBody.copy(player.root.position).setY(player.root.position.y + 1)
        : chaseVehicle ? clearanceBody.set(chaseVehicle.pos.x, chaseVehicle.pos.y + 0.3, chaseVehicle.pos.z) : clearanceBody.copy(feet).setY(feet.y + 0.9),
      velocity: vel, mode };
    viewClearance.update(camera, clearing, dt);
    if (multiplayerPlaying) remotePlayers.fadeLabels(camera);
    damageConsole.frame(camera);
    renderFrame();
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
    // A portal fires once on entry; a city whose tiles are not served yet only says so (gameplay/travel.ts).
    if (!travelling && !paused && !startScreen.active && !saveGame.loading && !fly?.active) {
      const hit = portalTriggers.update(feet.x, feet.y, feet.z, id => portalMarkers.groundOf(id));
      if (hit) void availability.confirm(hit.to, Date.now()).then(open => {
        if (open) depart(hit.to, hit);
        else portalMarkers.say(`${CITIES[hit.to].title} is coming soon: its streets are still being built.`, performance.now());
      });
    }
    for (const p of portalList) {
      const served = availability.served(p.to, Date.now());
      portalMarkers.setSoon(p.id, served === null ? null : !served);
    }
    portalMarkers.update(dt, camera, feet, now);
    minimap.setPortals(portalMarkers.markers());
    const playerName = startScreen.active || positionHud.characterHidden || !devLayers.nameTagsVisible ? undefined : multiplayer?.state.you?.nick;
    hud.update(now, playerName, () => {
      const en = engineToEN(feet.x, feet.y, feet.z);
      const tot = physics.totals();
      const ct = collisionTileAt(feet.x, feet.z);
      return { backend, fps: 1000 / frameEma, frameMs: frameEma, physMs: physEma,
        visTris: tiles.residentTris, visModels: tiles.residentModels,
        colTiles: tot.tiles, colTris: tot.tris, colMB: tot.colBytes / 1e6, colWasmMB: tot.estWasmBytes / 1e6,
        E: en.E, N: en.N, H: en.H, heading: yawToHeading(orbit.yaw), mode, speed: Math.hypot(vel.x, vel.z),
        ground: MATERIALS[groundMat]?.name ?? String(groundMat), tile: `c_${ct.i}_${ct.j}`, draft: coarse?.status,
        lights: vs?.lights ?? 0, perf };
    });
    input.setTouchContext(startScreen.active || fullscreen.blocking ? 'hidden'
      : menuOpen || missions.shop || pauseMenu.open ? 'menu' : paused ? 'paused' : recovery.lost ? 'lost' : mode);
    helpLearning.render(input, mode, !!fly?.active, paused, !!recovery.lost, !startScreen.active, now);
    world.loadingScreen.update(!startScreen.active, coarse?.status);
    perf.end();
    window.__render?.afterFrame?.();
  });

  return {
    dispose(travel = false) {
      autopilot.cancel('session ended'); driveMeThere.dispose(); input.driver = null;
      const snapshot: HotPlay = { save: startScreen.active || saveGame.loading ? null : saveGame.capture(), mode, drivenId, audio,
        camera: { yaw: orbit.yaw, pitch: orbit.pitch, distance: orbit.distance }, hudVisible: hud.visible,
        multiplayer: multiplayerPlaying };
      physics.updateInput({ vx: 0, vz: 0, jump: false }, null, false);
      renderer.setAnimationLoop(null);
      // A city switch hands the open socket to the next city's session (MultiplayerClient.adopt).
      if (travel) snapshot.socket = multiplayer?.detach() ?? null; else multiplayer?.disconnect();
      capture?.dispose();
      editor.dispose();
      devLayers.dispose();
      remotePlayers.dispose();
      audio.emit({ type: 'horns', vehicles: [] });
      vehicleBeams.dispose();
      tyreEffects.dispose();
      damageHud.dispose();
      walkers.dispose();
      parkedVisuals.dispose();
      poles.dispose();
      portalMarkers.dispose();
      scoreboard.dispose(); rounds.dispose();
      listeners.abort();
      saveGame.dispose();
      input.dispose();
      voice.dispose();
      minimap.dispose();
      if (coarse) coarse.onMapChange = null;
      fullscreen.dispose();
      disposePlayScene(root, body);
      return snapshot;
    },
    reload() {
      autosaveForReload();
      location.reload();
    },
  };
}
