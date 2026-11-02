/** Local vehicle gallery (vehicle-gallery.html): one canvas per fleet model, each on the wheel-articulation step
 *  block of the fleet workshop. The section in view gets the one live renderer and is driven with the game's own
 *  vehicle physics and bindings; the others keep a snapshot of their last frame. */
import * as THREE from 'three/webgpu';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { FLEET } from '../actors/fleet/catalog';
import { ParkedCarVisuals } from '../actors/fleet/visuals';
import { draftManifest } from '../world/manifest';
import { DEFAULT_BINDINGS, mapActions } from '../input/bindings';

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

const list = document.getElementById('list')!;
const views = FLEET.map((model, i) => {
  const section = document.createElement('section'), title = document.createElement('h2'), view = document.createElement('div');
  title.textContent = `${i + 1}. ${model.name} · ${model.body} · ${model.length.toFixed(2)} m`;
  view.className = 'view'; view.dataset.index = String(i);
  const still = document.createElement('canvas'); still.width = 960; still.height = 540; view.append(still);
  section.append(title, view); list.append(section);
  return { view, still };
});

type Active = { index: number; world: InstanceType<typeof R.World>; vehicle: InstanceType<typeof Vehicle>; visuals: ParkedCarVisuals };
let active: Active | null = null, pending = -1;

function snapshot(a: Active) {
  renderer.render(scene, camera);
  const { still } = views[a.index], ctx = still.getContext('2d')!;
  ctx.drawImage(renderer.domElement, 0, 0, still.width, still.height);
}

async function activate(index: number) {
  if (active?.index === index || pending === index) return;
  pending = index;
  if (active) { snapshot(active); active.visuals.dispose(); active.world.free(); active = null; }
  const model = FLEET[index], world = new R.World({ x: 0, y: -9.81, z: 0 }); world.timestep = 1 / 60;
  const groups = (GROUP.STATIC_SOLID << 16) | GROUP.VEHICLE;
  world.createCollider(R.ColliderDesc.cuboid(200, 0.5, 200).setTranslation(0, -0.5, 0).setCollisionGroups(groups));
  world.createCollider(R.ColliderDesc.cuboid(STEP.hx, STEP.hy, STEP.hz).setTranslation(STEP.x, STEP.y, STEP.z).setCollisionGroups(groups));
  const vehicle = new Vehicle(R, world, START, 0, new CurbQueries(R, world));
  const manifest = draftManifest(location.origin + '/');
  manifest.vehicles = [{ object_id: 'drive', model: model.id, color: COLORS[index % COLORS.length], x: 0, y: 0, z: 0, yaw: 0,
    width: model.width, height: model.height, length: model.length, clear_front_m: 5, clear_rear_m: 5 }];
  const visuals = new ParkedCarVisuals(scene); await visuals.load(manifest); visuals.takeOver('drive');
  if (pending !== index) { visuals.dispose(); world.free(); return; }
  for (let i = 0; i < 60; i++) { vehicle.step(1 / 60, null); world.step(); }
  const { view } = views[index]; view.append(renderer.domElement);
  for (const v of views) v.view.classList.toggle('active', v.view === view);
  resize(); placeCamera(vehicle.state().pos, true);
  active = { index, world, vehicle, visuals }; pending = -1;
}

function resize() {
  const view = renderer.domElement.parentElement; if (!view) return;
  renderer.setSize(view.clientWidth, view.clientHeight, false);
  camera.aspect = view.clientWidth / Math.max(1, view.clientHeight); camera.updateProjectionMatrix();
}
addEventListener('resize', resize);

const forward = new THREE.Vector3(), target = new THREE.Vector3(), wanted = new THREE.Vector3();
function placeCamera(pos: { x: number; y: number; z: number }, snap = false, rot?: { x: number; y: number; z: number; w: number }, dt = 1 / 60) {
  forward.set(1, 0, 0);
  if (rot) forward.applyQuaternion(new THREE.Quaternion(rot.x, rot.y, rot.z, rot.w));
  forward.y = 0; forward.normalize();
  target.set(pos.x, pos.y + 0.6, pos.z);
  wanted.copy(target).addScaledVector(forward, -6.5).add(new THREE.Vector3(1.5, 2.2, 3));
  if (snap) camera.position.copy(wanted); else camera.position.lerp(wanted, 1 - Math.exp(-dt * 3));
  camera.lookAt(target);
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

const keys = new Set<string>();
const DRIVE_KEYS = new Set(['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Space']);
addEventListener('keydown', e => { keys.add(e.code); if (DRIVE_KEYS.has(e.code)) e.preventDefault(); });
addEventListener('keyup', e => keys.delete(e.code));
addEventListener('blur', () => keys.clear());

let last = performance.now(), carry = 0, resetHeld = false;
renderer.setAnimationLoop(() => {
  const now = performance.now(), dt = Math.min(0.1, (now - last) / 1000); last = now;
  if (!active) return;
  const pad = [...(navigator.getGamepads?.() ?? [])].find(p => p) ?? null;
  const a = mapActions(pad, keys, DEFAULT_BINDINGS);
  if (a.vehicleReset > 0.5 && !resetHeld) active.vehicle.reset(START, 0);
  resetHeld = a.vehicleReset > 0.5;
  const input = { throttle: a.throttle, steer: a.steer, brake: a.brake, handbrake: a.handbrake > 0.5 };
  for (carry += dt; carry >= 1 / 60; carry -= 1 / 60) { active.vehicle.step(1 / 60, input); active.world.step(); }
  const state = { ...active.vehicle.state(), id: 'drive' };
  placeCamera(state.pos, false, state.rot, dt);
  active.visuals.setView(camera); active.visuals.update([state]);
  renderer.render(scene, camera);
});
