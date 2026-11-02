/** Local vehicle gallery (vehicle-gallery.html): one canvas per fleet model, each on the wheel-articulation step
 *  block of the fleet workshop. The section in view gets the one live renderer and is driven with the game's own
 *  vehicle physics, input (keyboard, gamepad, click to capture the mouse) and chase camera; the others keep a
 *  snapshot of their last frame. */
import * as THREE from 'three/webgpu';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { FLEET } from '../actors/fleet/catalog';
import { AUTHORED_MODELS } from '../actors/fleet/registry';
import { CarVisual } from '../actors/car';
import { ScooterVisual } from '../actors/scooter';
import { TruckVisual } from '../actors/truck';
import type { VehicleState } from '../physics/protocol';
import { ParkedCarVisuals } from '../actors/fleet/visuals';
import { draftManifest } from '../world/manifest';
import { Input } from '../input';
import { ChaseCamera } from '../vehicle/chaseCamera';

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
const input = new Input(renderer.domElement);

type Entry = { kind: 'fleet'; model: (typeof FLEET)[number] } | { kind: 'car' | 'scooter' | 'truck'; name: string };
const authored = new Set(AUTHORED_MODELS.map(m => m.id));
const ENTRIES: Entry[] = [
  { kind: 'car', name: 'Player car (sedan)' }, { kind: 'scooter', name: 'Scooter' }, { kind: 'truck', name: 'Trophy truck' },
  ...FLEET.filter(m => authored.has(m.id)).map(model => ({ kind: 'fleet' as const, model })),
  ...FLEET.filter(m => !authored.has(m.id)).map(model => ({ kind: 'fleet' as const, model })),
];
const list = document.getElementById('list')!;
const views = ENTRIES.map((entry, i) => {
  const section = document.createElement('section'), title = document.createElement('h2'), view = document.createElement('div');
  title.textContent = entry.kind === 'fleet'
    ? `${i + 1}. ${entry.model.name} · ${entry.model.body} · ${entry.model.length.toFixed(2)} m${authored.has(entry.model.id) ? ' · rebuilt real model' : ''}`
    : `${i + 1}. ${entry.name}`;
  view.className = 'view'; view.dataset.index = String(i);
  const still = document.createElement('canvas'); still.width = 960; still.height = 540; view.append(still);
  section.append(title, view); list.append(section);
  return { view, still };
});

type Visual = { update(state: VehicleState, dt: number): void; dispose(): void };
type Active = { index: number; world: InstanceType<typeof R.World>; vehicle: InstanceType<typeof Vehicle>; visual: Visual };

async function makeVisual(entry: Entry, index: number): Promise<Visual> {
  if (entry.kind === 'car') {
    const v = new CarVisual(scene); await v.load('/assets/car/sedan.glb');
    return { update: s => v.update(s), dispose: () => v.root.removeFromParent() };
  }
  if (entry.kind === 'scooter') {
    const v = new ScooterVisual(scene); await v.load('/assets/vehicles/scooter.glb'); scene.add(v.root); v.root.visible = true;
    return { update: (s, dt) => v.update(dt, s), dispose: () => v.root.removeFromParent() };
  }
  if (entry.kind === 'truck') {
    const v = new TruckVisual(scene); await v.load(); v.root.visible = true;
    return { update: s => v.update(s), dispose: () => v.root.removeFromParent() };
  }
  const model = (entry as Extract<Entry, { kind: 'fleet' }>).model, manifest = draftManifest(location.origin + '/');
  manifest.vehicles = [{ object_id: 'drive', model: model.id, color: COLORS[index % COLORS.length], x: 0, y: 0, z: 0, yaw: 0,
    width: model.width, height: model.height, length: model.length, clear_front_m: 5, clear_rear_m: 5 }];
  const visuals = new ParkedCarVisuals(scene); await visuals.load(manifest); visuals.takeOver('drive');
  return { update: s => { visuals.setView(camera); visuals.update([{ ...s, id: 'drive' }]); }, dispose: () => visuals.dispose() };
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
  world.createCollider(R.ColliderDesc.cuboid(STEP.hx, STEP.hy, STEP.hz).setTranslation(STEP.x, STEP.y, STEP.z).setCollisionGroups(groups));
  const kind = entry.kind === 'fleet' ? 'car' : entry.kind;
  const start = kind === 'truck' ? { ...START, y: START.y + 0.5 } : START;
  const vehicle = new Vehicle(R, world, start, 0, new CurbQueries(R, world), { kind });
  const visual = await makeVisual(entry, index);
  if (pending !== index) { visual.dispose(); world.free(); return; }
  for (let i = 0; i < 60; i++) { vehicle.step(1 / 60, null); world.step(); }
  const { view } = views[index]; view.append(renderer.domElement);
  for (const v of views) v.view.classList.toggle('active', v.view === view);
  resize(); openingView(vehicle.state()); followSun(vehicle.state().pos);
  active = { index, world, vehicle, visual }; pending = -1;
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
  chase.yaw = 0.70; chase.pitch = -0.3;
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

let last = performance.now(), carry = 0;
renderer.setAnimationLoop(() => {
  const now = performance.now(), dt = Math.min(0.1, (now - last) / 1000); last = now;
  input.update(dt);
  if (!active) return;
  const a = input.actions;
  if (input.consume('vehicleReset')) { active.vehicle.reset(START, 0); openingView(active.vehicle.state()); }
  const drive = { throttle: a.throttle, steer: a.steer, brake: a.brake, handbrake: a.handbrake > 0.5 };
  for (carry += dt; carry >= 1 / 60; carry -= 1 / 60) { active.vehicle.step(1 / 60, drive); active.world.step(); }
  const state = { ...active.vehicle.state(), id: 'drive' };
  chase.update(dt, state, input.lookX, input.lookY, input.consume('cameraReset'));
  followSun(state.pos);
  active.visual.update(state, dt);
  renderer.render(scene, camera);
});
