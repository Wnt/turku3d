/** Vehicle gallery (vehicle-gallery.html, also on the public build): the starter car, scooter, trophy truck and
 *  every real fleet model with its name, licence, source and triangles per LOD, each on a 15 cm step block that
 *  articulates the wheels. The section in view gets the one live renderer and is driven with the game's own vehicle
 *  physics (the model's own tuning, vehicle/modelTuning.ts), input and chase camera; P cycles the paint (liveries
 *  are their own models). The others keep a snapshot of their last frame. */
import * as THREE from 'three/webgpu';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { AUTHORED_MODELS, type AuthoredModel } from '../actors/fleet/registry';
import { PLAYER_COLOR, PLAYER_MODEL } from '../actors/fleet/catalog';
import { modelTuning } from '../vehicle/modelTuning';
import { CarVisual } from '../actors/car';
import { ScooterVisual } from '../actors/scooter';
import { TruckVisual } from '../actors/truck';
import { configureTrophyAsset } from '../actors/trophy4400/asset';
import type { VehicleState } from '../physics/protocol';
import { VehicleSignals, LIGHT_LABELS } from '../vehicle/signals';
import { VehicleBeams } from '../vehicle/beams';
import { GameAudio } from '../audio';
import { nightUniforms } from '../render/nightLights';
import { fleetNight } from '../actors/fleet/material';
import { ParkedCarVisuals } from '../actors/fleet/visuals';
import { draftManifest } from '../world/manifest';
import { Input } from '../input';
import { ChaseCamera } from '../vehicle/chaseCamera';
import { PlayerVisual } from '../actors/player';
import { Drivers, fleetDriverLayout } from '../actors/driver/drivers';
import type { DriverLayout } from '../actors/driver/layout';

const R = await import('@dimforge/rapier3d');
const { Vehicle } = await import('../vehicle/vehicle');
const { CurbQueries } = await import('../vehicle/curbQueries');
const { GROUP } = await import('../shared/col/materials');

const COLORS = ['silver', 'white', 'charcoal', 'grey', 'black', 'blue', 'red'];
// The workshop's motion fixture: a 6 × 4 m block, 15 cm high, with the car straddling its edge (yaw 0 faces +x).
const STEP = { x: 3, y: 0.075, z: -1, hx: 3, hy: 0.075, hz: 2 };
const START = { x: 0.3, y: 0.9, z: -1 };

const renderer = new THREE.WebGPURenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5)); renderer.shadowMap.enabled = true;
renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.1;
await renderer.init();
const scene = new THREE.Scene(); scene.background = new THREE.Color('#858e95');
const room = new RoomEnvironment(), pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = (await pmrem.fromSceneAsync(room)).texture; room.dispose();
scene.add(new THREE.HemisphereLight(0xd7e5ff, 0x68625b, 2));
const sun = new THREE.DirectionalLight(0xfff4dd, 3); sun.position.set(10, 15, 8); sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048); Object.assign(sun.shadow.camera, { left: -20, right: 20, top: 20, bottom: -20 });
sun.shadow.normalBias = 0.035; scene.add(sun, sun.target);
const floor = new THREE.Mesh(new THREE.PlaneGeometry(400, 400), new THREE.MeshStandardMaterial({ color: 0x606568, roughness: 0.95 }));
floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
const block = new THREE.Mesh(new THREE.BoxGeometry(STEP.hx * 2, STEP.hy * 2, STEP.hz * 2), new THREE.MeshStandardMaterial({ color: 0x93938b, roughness: 0.9 }));
block.position.set(STEP.x, STEP.y, STEP.z); block.receiveShadow = true; block.castShadow = true; scene.add(block);
const camera = new THREE.PerspectiveCamera(43, 16 / 9, 0.1, 400);
// The open test floor has nothing to collide with, so the chase camera keeps its full distance.
const chase = new ChaseCamera(camera, { cameraCast: async (_origin, _dir, distance) => distance });
const audio = new GameAudio(); void audio.preload();
const input = new Input(renderer.domElement, () => { void audio.unlock(); });
const signals = new VehicleSignals();
// Open floor: nothing blocks the beams.
const beams = new VehicleBeams(scene, { raycast: async () => null });
const hemi = scene.children.find(o => (o as THREE.HemisphereLight).isHemisphereLight) as THREE.HemisphereLight;
let night = false;
function setNight(on: boolean) {
  night = on; nightUniforms.night.value = on ? 1 : 0; fleetNight.value = on ? 1 : 0;
  hemi.intensity = on ? 0.08 : 2; sun.intensity = on ? 0.05 : 3; scene.environmentIntensity = on ? 0.05 : 1;
  scene.background = new THREE.Color(on ? '#10151b' : '#858e95');
}
addEventListener('keydown', e => { if (e.code === 'KeyN' && !e.repeat) setNight(!night); });
// P: next paint variant of the model in view (taxi, police and other liveries are their own fleet models).
addEventListener('keydown', e => {
  if (e.code !== 'KeyP' || e.repeat || !active || ENTRIES[active.index].kind !== 'fleet') return;
  const a = active; paint[a.index] = (paint[a.index] + 1) % COLORS.length;
  a.visual.dispose();
  void makeVisual(ENTRIES[a.index], a.index).then(visual => { if (active === a) a.visual = visual; else visual.dispose(); status.textContent = `paint ${COLORS[paint[a.index]]}`; });
});
const status = document.getElementById('status')!;
// Every vehicle is shown with its seated, animated driver (actors/driver), cloned from the game's player character.
const drivers = new Drivers();
const character = new PlayerVisual(new THREE.Group(), async () => null);
void character.load(`${import.meta.env.BASE_URL}assets/character/player.glb`, renderer)
  .then(() => { if (character.template) drivers.setCharacter(character.template.scene, character.template.clips, renderer); });

type Entry = { kind: 'fleet'; entry: AuthoredModel } | { kind: 'car' | 'scooter' | 'truck'; name: string };
const ENTRIES: Entry[] = [
  { kind: 'car', name: `Starter car: ${AUTHORED_MODELS.find(m => m.id === PLAYER_MODEL)?.fleet.name} (${PLAYER_MODEL})` },
  { kind: 'scooter', name: 'Scooter' }, { kind: 'truck', name: 'Trophy truck (#19 4400)' },
  ...[...AUTHORED_MODELS].sort((a, b) => a.fleet.family.localeCompare(b.fleet.family) || a.fleet.name.localeCompare(b.fleet.name))
    .map(entry => ({ kind: 'fleet' as const, entry })),
];
const paint = ENTRIES.map((_, i) => i % COLORS.length);
type Source = { title: string; url: string; author: string; licence: string; licenceUrl: string };
const list = document.getElementById('list')!;
const views = ENTRIES.map((entry, i) => {
  const section = document.createElement('section'), title = document.createElement('h2'), view = document.createElement('div');
  if (entry.kind === 'fleet') {
    const m = entry.entry, src = (m as AuthoredModel & { source?: Source }).source;
    title.textContent = `${i + 1}. ${m.fleet.name} · ${m.id} · ${m.fleet.family} · ${m.fleet.length.toFixed(2)} m · triangles ${m.triangles.join(' / ')}`;
    const credit = document.createElement('p'); credit.className = 'credit';
    if (src) {
      const link = document.createElement('a'); link.href = src.url; link.textContent = src.title; link.target = '_blank';
      const lic = document.createElement('a'); lic.href = src.licenceUrl; lic.textContent = src.licence; lic.target = '_blank';
      credit.append(link, ` by ${src.author}, `, lic, ', modified for Turku3D');
    } else credit.textContent = 'Original model authored for Turku3D (Blenderwright MCP), project-owned';
    section.append(title, credit);
  } else { title.textContent = `${i + 1}. ${entry.name}`; section.append(title); }
  view.className = 'view'; view.dataset.index = String(i);
  view.dataset.id = entry.kind === 'fleet' ? entry.entry.id : entry.kind;
  const still = document.createElement('canvas'); still.width = 960; still.height = 540; view.append(still);
  section.append(view); list.append(section);
  return { view, still };
});

type Visual = { update(state: VehicleState, dt: number): void; dispose(): void;
  /** Where the driver sits: the vehicle's fleet-frame Object3D and its layout (none on the scooter). */
  driver?(): { frame: THREE.Object3D; layout: DriverLayout } | null };
type Active = { index: number; world: InstanceType<typeof R.World>; vehicle: InstanceType<typeof Vehicle>; visual: Visual; start: typeof START };

async function makeVisual(entry: Entry, index: number): Promise<Visual> {
  if (entry.kind === 'car') {
    const v = new CarVisual(scene, PLAYER_MODEL, PLAYER_COLOR); await v.load();
    return { update: s => v.update(s), dispose: () => v.root.removeFromParent(),
      driver: () => v.frame && { frame: v.frame, layout: fleetDriverLayout(PLAYER_MODEL) } };
  }
  if (entry.kind === 'scooter') {
    const v = new ScooterVisual(scene); await v.load('/assets/vehicles/scooter.glb'); scene.add(v.root); v.root.visible = true;
    return { update: (s, dt) => v.update(dt, s), dispose: () => v.root.removeFromParent() };
  }
  if (entry.kind === 'truck') {
    configureTrophyAsset(import.meta.env.BASE_URL, renderer); const v = new TruckVisual(scene); await v.load(); v.root.visible = true;
    return { update: s => v.update(s), dispose: () => v.root.removeFromParent(),
      driver: () => v.driverLayout && { frame: v.driverFrame, layout: v.driverLayout } };
  }
  const model = (entry as Extract<Entry, { kind: 'fleet' }>).entry.fleet, manifest = draftManifest(location.origin + '/');
  manifest.vehicles = [{ object_id: 'drive', model: model.id, color: COLORS[paint[index]], x: 0, y: 0, z: 0, yaw: 0,
    width: model.width, height: model.height, length: model.length, clear_front_m: 5, clear_rear_m: 5 }];
  const visuals = new ParkedCarVisuals(scene); await visuals.load(manifest); visuals.takeOver('drive');
  return { update: s => { visuals.setView(camera); visuals.update([{ ...s, id: 'drive' }]); }, dispose: () => visuals.dispose(),
    driver: () => { const d = visuals.driven('drive'); return d && { frame: d.actor, layout: fleetDriverLayout(d.model) }; } };
}
let active: Active | null = null, pending = -1;

function snapshot(a: Active) {
  renderer.render(scene, camera);
  const { still } = views[a.index], ctx = still.getContext('2d')!;
  ctx.drawImage(renderer.domElement, 0, 0, still.width, still.height);
}

async function activate(index: number) {
  if (active?.index === index || pending === index) return;
  pending = index;
  if (active) { snapshot(active); active.visual.dispose(); active.world.free(); active = null; }
  const entry = ENTRIES[index], world = new R.World({ x: 0, y: -9.81, z: 0 }); world.timestep = 1 / 60;
  const groups = (GROUP.STATIC_SOLID << 16) | GROUP.VEHICLE;
  world.createCollider(R.ColliderDesc.cuboid(200, 0.5, 200).setTranslation(0, -0.5, 0).setCollisionGroups(groups));
  // The trophy truck gets a 75 cm platform (5× the car step) with only its front-right wheel on the corner.
  const height = entry.kind === 'truck' ? 0.75 : STEP.hy * 2;
  world.createCollider(R.ColliderDesc.cuboid(STEP.hx, height / 2, STEP.hz).setTranslation(STEP.x, height / 2, STEP.z).setCollisionGroups(groups));
  block.scale.y = height / (STEP.hy * 2); block.position.y = height / 2;
  const kind = entry.kind === 'fleet' ? 'car' : entry.kind;
  const start = kind === 'truck' ? { x: -1.0, y: 1.5, z: -3.75 } : START;
  const tuning = entry.kind === 'fleet' ? modelTuning(entry.entry.id) : entry.kind === 'car' ? modelTuning(PLAYER_MODEL) : undefined;
  const vehicle = new Vehicle(R, world, start, 0, new CurbQueries(R, world), { kind, ...(tuning ? { tuning } : {}) });
  const visual = await makeVisual(entry, index);
  if (pending !== index) { visual.dispose(); world.free(); return; }
  // Settle on the brakes (the soft truck bounces and would roll off the platform) before the opening view.
  const hold = { throttle: 0, steer: 0, brake: 0, handbrake: true }; // brake at a standstill means reverse
  for (let i = 0; i < 240; i++) { vehicle.step(1 / 60, hold); world.step(); }
  const { view } = views[index]; view.append(renderer.domElement);
  for (const v of views) v.view.classList.toggle('active', v.view === view);
  resize(); openingView(vehicle.state()); followSun(vehicle.state().pos); signals.enter();
  active = { index, world, vehicle, visual, start }; pending = -1;
}

function resize() {
  const view = renderer.domElement.parentElement; if (!view) return;
  renderer.setSize(view.clientWidth, view.clientHeight, false);
  camera.aspect = view.clientWidth / Math.max(1, view.clientHeight); camera.updateProjectionMatrix();
}
addEventListener('resize', resize);

// Opening view: front three-quarter from the car's left, looking slightly down onto the step (Jonni's framing).
// The chase camera swings back behind the car once it drives.
function openingView(state: ReturnType<InstanceType<typeof Vehicle>['state']>) {
  chase.update(0, state, 0, 0, true);
  // The truck is seen from behind and above, so the raised front-right wheel on the platform is in view.
  if (state.kind === 'truck') chase.pitch = -0.85; else { chase.yaw = 0.70; chase.pitch = -0.3; }
  chase.update(0, state, 0, 0, false);
}

function followSun(pos: { x: number; z: number }) {
  sun.position.set(pos.x + 10, 15, pos.z + 8); sun.target.position.set(pos.x, 0, pos.z);
}

// The section most in view is the active one.
const ratios = new Map<number, number>();
const observer = new IntersectionObserver(entries => {
  for (const e of entries) ratios.set(Number((e.target as HTMLElement).dataset.index), e.intersectionRatio);
  let best = -1, ratio = 0.35;
  for (const [i, r] of ratios) if (r > ratio) { best = i; ratio = r; }
  if (best >= 0) void activate(best);
}, { threshold: [0, 0.25, 0.5, 0.75, 1] });
for (const v of views) observer.observe(v.view);

// Arrow keys and Space drive the car; they must not scroll the page.
const DRIVE_KEYS = new Set(['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Space']);
addEventListener('keydown', e => { if (DRIVE_KEYS.has(e.code)) e.preventDefault(); });

const listenerForward = new THREE.Vector3();
// Test hook: the active vehicle's state, the input the game would read, and for scripted views a held drive input
// (`hold`, replacing the controls), the chase camera and the drivers.
const hook = { state: () => active?.vehicle.state(), actions: () => input.actions, source: () => input.source,
  hold: null as null | { throttle: number; steer: number; brake: number; handbrake?: boolean; horn?: boolean }, drivers: () => drivers.snapshot(),
  /** A fixed camera relative to the vehicle (yaw about +Y from its forward axis, degrees; metres; `ahead`: the point
   *  looked at that far forward of the vehicle's centre, e.g. a bus's cab), or the chase. */
  view: null as null | { yaw: number; pitch: number; distance: number; height: number; ahead?: number },
  reset: () => { if (active) { active.vehicle.reset(active.start, 0); openingView(active.vehicle.state()); } }, scene };
Object.assign(window, { vehicleGallery: hook });
let last = performance.now(), carry = 0;
renderer.setAnimationLoop(() => {
  const now = performance.now(), dt = Math.min(0.1, (now - last) / 1000); last = now;
  input.update(dt);
  if (!active) return;
  const a = input.actions;
  if (input.consume('vehicleReset')) { active.vehicle.reset(active.start, 0); openingView(active.vehicle.state()); }
  signals.update(true, input.consume('lights'), a.horn > 0.5 || !!hook.hold?.horn);
  const held = hook.hold;
  const drive = held ? { throttle: held.throttle, steer: held.steer, brake: held.brake, handbrake: !!held.handbrake, lights: signals.lights, horn: signals.horn }
    : { throttle: a.throttle, steer: a.steer, brake: a.brake, handbrake: a.handbrake > 0.5, lights: signals.lights, horn: signals.horn };
  for (carry += dt; carry >= 1 / 60; carry -= 1 / 60) { active.vehicle.step(1 / 60, drive); active.world.step(); }
  const state = { ...active.vehicle.state(), id: 'drive', lights: signals.lights, horn: signals.horn };
  chase.update(dt, state, input.lookX, input.lookY, input.consume('cameraReset'));
  if (hook.view) {
    const v = hook.view, q = new THREE.Quaternion(state.rot.x, state.rot.y, state.rot.z, state.rot.w);
    const forward = new THREE.Vector3(1, 0, 0).applyQuaternion(q).setY(0).normalize();
    const target = new THREE.Vector3(state.pos.x, state.pos.y + v.height, state.pos.z).addScaledVector(forward, v.ahead ?? 0);
    const dir = forward.applyAxisAngle(new THREE.Vector3(0, 1, 0), v.yaw * Math.PI / 180);
    camera.position.copy(target).addScaledVector(dir, v.distance * Math.cos(v.pitch * Math.PI / 180)).setY(target.y + v.distance * Math.sin(v.pitch * Math.PI / 180));
    camera.lookAt(target);
  }
  followSun(state.pos);
  active.visual.update(state, dt);
  const seat = active.visual.driver?.();
  if (seat) drivers.seat('gallery', seat.frame, seat.layout, { velocity: state.linvel, wheels: state.wheels, impact: state.impact?.id, horn: state.horn });
  drivers.finish(camera, now);
  beams.update(state, now);
  camera.getWorldDirection(listenerForward);
  audio.emit({ type: 'listener', position: camera.position, forward: listenerForward, up: camera.up, paused: false });
  audio.emit({ type: 'horns', vehicles: signals.horn ? [{ id: 'gallery', kind: active.vehicle.kind, position: state.pos }] : [] });
  const pads = [...(navigator.getGamepads?.() ?? [])].filter((g): g is Gamepad => !!g)
    .map(g => `${g.id.slice(0, 40)} (${g.mapping || 'no standard mapping'})`).join(', ') || 'none seen yet: press a controller button';
  status.textContent = `${LIGHT_LABELS[signals.lights]}${signals.horn ? ' · horn' : ''}${night ? ' · night' : ''} · input: ${input.source} · controllers: ${pads}`;
  renderer.render(scene, camera);
});
