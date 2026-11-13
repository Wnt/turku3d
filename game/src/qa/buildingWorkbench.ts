// Building workbench (dev server: vite.workbench.config.ts, /building-workbench.html): one generated building in the
// game's renderer, from the camera of a reference view, with the photo beside it.
//   ?name=NAME            the build under $TURKU3D_WORK/workbench/NAME (pipeline: turku3d.world.kinds.building.workbench)
//   &time=HH:MM &weather=clear|overcast|... &quality=medium &shot=1 (no status line) &orbit=1 (free camera)
// The page reloads the building when its meta.json changes; __workbench.refresh() does it on demand.
import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { createRenderer } from '../render/renderer';
import { Environment } from '../render/environment';
import { DayClock } from '../render/dayClock';
import { PostProcessing } from '../render/post';
import { parseQuality } from '../render/quality';
import { WEATHER_PRESETS, type WeatherName } from '../render/weather/schedule';
import { CITIES } from '../core/city';
import { buildingMaterial, type BuildingMaterialParams } from '../world/location/buildingMaterials';
import { markVertices } from '../world/location/materials';

declare const __WORKBENCH_ROOT__: string;
declare const __WORKBENCH_REFS__: string;

interface Pose { E: number; N: number; H: number; yaw: number; pitch: number; roll: number; fov: number }
interface Meta {
  name: string; id: string; revision: number; origin: [number, number, number];
  view: { id: string; image: string; photo?: string; width: number; height: number; pose: Pose } | null;
  report: string[]; stats: { triangles: number; materials: number; bytes: number };
  timings_ms: Record<string, number>;
}

const query = new URLSearchParams(location.search);
const name = query.get('name') ?? 'building';
const quality = parseQuality(query.get('quality'));
const root = `/@fs${__WORKBENCH_ROOT__}/${name}`;
if (query.get('shot') === '1') document.body.classList.add('shot');
const status = document.getElementById('status')!;

async function fetchMeta(): Promise<Meta> {
  return await (await fetch(`${root}/meta.json?t=${Date.now()}`, { cache: 'no-store' })).json();
}

let meta = await fetchMeta();
const width = meta.view?.width ?? 960, height = meta.view?.height ?? 720;
const photoPanel = document.getElementById('photo')!, renderPanel = document.getElementById('render')!;
for (const panel of [photoPanel, renderPanel]) { panel.style.width = `${width}px`; panel.style.height = `${height}px`; }
// A view of qa/views shows its reference photo; any other view brings its own photo beside the GLB.
if (meta.view) (document.getElementById('reference') as HTMLImageElement).src = meta.view.photo
  ? `${root}/${meta.view.photo}?v=${meta.revision}` : `/@fs${__WORKBENCH_REFS__}/${meta.view.image.split('/').pop()}`;
else photoPanel.style.display = 'none';

const { renderer, backend } = await createRenderer('webgpu', renderPanel, { quality, qa: true });
renderer.setPixelRatio(1);
renderer.setSize(width, height);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(55, width / height, 0.1, 6000);
const env = new Environment(scene, renderer, quality);
const time = query.get('time');
const clock = new DayClock(env, time
  ? { kind: 'hour', hour: Number(time.split(':')[0]) + Number(time.split(':')[1] ?? 0) / 60 }
  : { kind: 'date', date: new Date('2026-06-15T14:00:00+03:00') }, false);
env.setWeather(WEATHER_PRESETS[(query.get('weather') ?? 'clear') as WeatherName] ?? WEATHER_PRESETS.clear);
const post = new PostProcessing(renderer, scene, camera, quality, 'taa');

const ground = new THREE.Mesh(new THREE.PlaneGeometry(4000, 4000).rotateX(-Math.PI / 2),
  new THREE.MeshStandardNodeMaterial({ color: 0x55585b, roughness: 0.95 }));
ground.receiveShadow = true;
scene.add(ground);

const controls = query.get('orbit') === '1' ? new OrbitControls(camera, renderer.domElement) : null;
const loader = new GLTFLoader();
let building: THREE.Object3D | null = null;
let drawn = 0, frames = 0;

function applyPose(pose: Pose) {
  const { E: E0, N: N0 } = CITIES.turku.origin, d = Math.PI / 180;
  camera.position.set(pose.E - E0, pose.H, -(pose.N - N0));
  camera.rotation.set(pose.pitch * d, -pose.yaw * d, -(pose.roll ?? 0) * d, 'YXZ');
  camera.fov = pose.fov; camera.updateProjectionMatrix();
  // Street View cameras ride about 2.5 m above the carriageway.
  ground.position.set(camera.position.x, pose.H - 2.5, camera.position.z);
}

async function load(next: Meta) {
  const gltf = await loader.loadAsync(`${root}/building.glb?rev=${next.revision}`);
  const model = gltf.scene;
  model.position.set(next.origin[0], next.origin[1], next.origin[2]);
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    const source = mesh.material as THREE.Material;
    const turku = source.userData?.turku as { family: string; params: BuildingMaterialParams } | undefined;
    if (turku) {
      const drawn = buildingMaterial(turku.family, turku.params);
      mesh.material = drawn.material;
      markVertices(mesh.geometry, drawn.vertex);
    }
    mesh.castShadow = true; mesh.receiveShadow = true;
  });
  if (building) {
    scene.remove(building);
    building.traverse(object => (object as THREE.Mesh).geometry?.dispose());
  }
  building = model; scene.add(model);
  meta = next; drawn = next.revision;
  if (next.view) applyPose(next.view.pose);
  else {
    const box = new THREE.Box3().setFromObject(model), centre = box.getCenter(new THREE.Vector3());
    camera.position.set(centre.x + 30, box.min.y + 8, centre.z + 30); camera.lookAt(centre);
    ground.position.set(centre.x, box.min.y + 0.3, centre.z);
  }
  if (controls) { controls.target.copy(new THREE.Box3().setFromObject(model).getCenter(new THREE.Vector3())); controls.update(); }
  const t = next.timings_ms;
  status.textContent = `${next.id} · ${next.stats.triangles.toLocaleString()} triangles · ${next.stats.materials} materials · built in ${t.total} ms · ${backend} ${quality}`
    + (next.report.length ? `\n${next.report.slice(0, 6).join('\n')}` : '');
}

const deferred = renderer as unknown as {
  asyncBuilds?: { readonly queued: number; settled(): Promise<void> };
  asyncPipelines?: { settled(): Promise<void> };
};
const after = (count: number) => new Promise<void>(resolve => {
  const target = frames + count, wait = () => frames >= target ? resolve() : requestAnimationFrame(wait);
  wait();
});

/** The game's renderer builds a draw's program and pipeline after the first frame that needs it and leaves the draw
 *  out until then (render/asyncNodeBuilds.ts, render/asyncPipelines.ts): a new material arrives some frames late.
 *  Resolves once nothing is queued and the number of draws has stopped growing. */
async function everythingDrawn() {
  let calm = 0, calls = -1;
  for (let round = 0; round < 400 && calm < 3; round++) {
    await deferred.asyncBuilds?.settled();
    await deferred.asyncPipelines?.settled();
    await after(4);
    const now = renderer.info.render.drawCalls;
    calm = (deferred.asyncBuilds?.queued ?? 0) === 0 && now === calls ? calm + 1 : 0;
    calls = now;
  }
}

/** Reload the building if its build changed; resolves once all of it is drawn and the image has settled. */
async function refresh(settleFrames = 30) {
  const started = performance.now();
  const next = await fetchMeta();
  const changed = next.revision !== drawn;
  if (changed) await load(next);
  await everythingDrawn();
  await after(settleFrames);
  return { revision: drawn, changed, draws: renderer.info.render.drawCalls, ms: Math.round(performance.now() - started) };
}

await load(meta);

renderer.info.autoReset = false;
function loop() {
  controls?.update();
  env.follow(camera.position);
  env.updateIBL();
  env.updateShadows(false);
  post.grade(env.sample);
  renderer.info.reset();
  post.render();
  frames++;
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);
if (query.get('shot') !== '1') setInterval(() => { void refresh(0).catch(() => undefined); }, 500);
Object.assign(window, { __workbench: { refresh, meta: () => meta, camera, scene, env, clock, renderer, frames: () => frames } });
