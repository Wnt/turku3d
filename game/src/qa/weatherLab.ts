// Small GPU correctness fixture for rain depth, sheltered ground and puddle rays; no tile or physics startup.
import * as THREE from 'three/webgpu';
import { createRenderer } from '../render/renderer';
import { Environment } from '../render/environment';
import { DayClock } from '../render/dayClock';
import { PostProcessing } from '../render/post';
import { WeatherClock, type WeatherName } from '../render/weather/schedule';
import { Rain } from '../render/weather/rain';
import { wetSurface, weatherUniforms } from '../render/weather/surfaces';

const errors: string[] = [];
addEventListener('error', e => errors.push(e.error?.stack ?? e.message));
addEventListener('unhandledrejection', e => errors.push(e.reason?.stack ?? String(e.reason)));
const { renderer } = await createRenderer('webgpu', document.body, { qa: true, quality: 'medium' });
renderer.shadowMap.enabled = false;
const device = (renderer.backend as unknown as { device?: GPUDevice }).device;
const captureTarget = new THREE.RenderTarget(innerWidth, innerHeight, { type: THREE.UnsignedByteType });
captureTarget.texture.colorSpace = THREE.SRGBColorSpace;
const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, .1, 1000);
camera.position.set(0, 1.75, 12); camera.lookAt(0, 2, -30); camera.updateMatrixWorld();
const env = new Environment(scene, renderer, 'medium');
const clock = new DayClock(env, { kind: 'hour', hour: 13 }, false);
const weather = new WeatherClock(Date.now, undefined, 'heavy rain');
const city = new THREE.Group();
const materials = new Map<string, THREE.MeshStandardNodeMaterial>();
const material = (colour: string, facade = false) => {
  const key = `${colour}:${facade}`;
  let value = materials.get(key);
  if (!value) { value = wetSurface(new THREE.MeshStandardNodeMaterial({ color: colour, roughness: .85 }), facade); materials.set(key, value); }
  return value;
};
function box(x: number, y: number, z: number, width: number, height: number, depth: number, colour: string) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, height, depth), material(colour, height > 3));
  mesh.position.set(x, y, z); mesh.receiveShadow = mesh.castShadow = true; city.add(mesh); return mesh;
}
const ground = new THREE.Mesh(new THREE.PlaneGeometry(120, 140, 30, 35), material('#34383c'));
ground.rotation.x = -Math.PI / 2; ground.position.z = -35; ground.receiveShadow = true; city.add(ground);
box(-8, .07, -35, 4, .14, 90, '#888880'); box(8, .07, -35, 4, .14, 90, '#888880');
for (let row = 0; row < 5; row++) {
  box(-13, 6, -row * 15 - 10, 7, 12, 14, row % 2 ? '#be8b68' : '#e6d59f');
  box(13, 5, -row * 15 - 10, 7, 10, 14, row % 2 ? '#9da8b9' : '#bd9fa0');
  for (const side of [-1, 1]) for (let floor = 0; floor < 4; floor++) for (let bay = 0; bay < 4; bay++) {
    box(side * 9.45, 1.5 + floor * 2.7, -row * 15 - 4 - bay * 3, .12, 1.8, 1.3, '#273543');
  }
}
// A road-side roof and a bright wall make occlusion and reflected colour easy to inspect.
box(-5.5, 3, -4, 4, .2, 6, '#c0c3c6');
box(-5, 2, -18, 1.5, 4, .2, '#c13b24'); box(5, 2, -24, 1.5, 4, .2, '#315bb1');
scene.add(city);
const rain = new Rain(scene); rain.exposure.add(city);
const overlay = { overlay: (r: THREE.WebGPURenderer, c: THREE.Camera, depth: THREE.Texture) => rain.overlay(r, c, depth) };
let post = new PostProcessing(renderer, scene, camera, 'medium', 'taa', overlay);
let frames = 0, paused = false, lastIBL = -Infinity, submitted = false, lastFrame = -Infinity;
const api = { scene, camera, renderer, env, rain, weather, errors, get post() { return post; }, get frames() { return frames; },
  setWeather(name: WeatherName | 'live', seconds = 0) { weather.setWeather(name, seconds); },
  setHour(hour: number) { clock.setHour(hour); lastIBL = -Infinity; },
  pause(value: boolean) { paused = value; },
  async readFrame() {
    paused = true;
    if (device) await device.queue.onSubmittedWorkDone();
    const data = await renderer.readRenderTargetPixelsAsync(captureTarget, 0, 0, captureTarget.width, captureTarget.height);
    const canvas = document.createElement('canvas'); canvas.width = captureTarget.width; canvas.height = captureTarget.height;
    canvas.getContext('2d')!.putImageData(new ImageData(new Uint8ClampedArray(data), captureTarget.width, captureTarget.height), 0, 0);
    return canvas.toDataURL('image/png');
  },
  async puddleStats() {
    if (!post.puddles) return null;
    const target = (post.puddles.texture as unknown as { renderTarget: THREE.RenderTarget }).renderTarget;
    const data = await renderer.readRenderTargetPixelsAsync(target, 0, 0, target.width, target.height);
    let hits = 0, maxAlpha = 0;
    for (let i = 3; i < data.length; i += 4) {
      const alpha = data instanceof Uint16Array ? THREE.DataUtils.fromHalfFloat(data[i]) : Number(data[i]);
      if (alpha > .001) hits++;
      maxAlpha = Math.max(maxAlpha, alpha);
    }
    return { hits, maxAlpha, width: target.width, height: target.height };
  },
};
Object.assign(window, { __weatherLab: api });
let postSwap = Promise.resolve();
const focus = new THREE.Vector3(0, 0, -30);
renderer.setAnimationLoop(() => {
  if (paused || submitted || performance.now() - lastFrame < 100) return;
  lastFrame = performance.now();
  const sample = weather.update(); clock.update(); env.setWeather(sample); env.follow(focus);
  if (performance.now() - lastIBL > 2500) { env.updateIBL(); lastIBL = performance.now(); }
  weatherUniforms.wetness.value = sample.wetness; weatherUniforms.rain.value = sample.rain;
  weatherUniforms.seconds.value = Date.now() / 1000 % 4096;
  rain.update(camera, sample, Date.now() / 1000, env.nightFactor);
  env.updateShadows(true); post.grade(env.sample);
  renderer.setRenderTarget(captureTarget);
  try { post.render(); } finally { renderer.setRenderTarget(null); }
  frames++;
  if (device) { submitted = true; void device.queue.onSubmittedWorkDone().finally(() => { submitted = false; }); }
});
if (import.meta.hot) import.meta.hot.accept('../render/post', next => {
  if (!next) return;
  postSwap = postSwap.then(async () => {
    const wasPaused = paused; paused = true;
    if (device) await device.queue.onSubmittedWorkDone();
    post.dispose(); post = new next.PostProcessing(renderer, scene, camera, 'medium', 'taa', overlay);
    paused = wasPaused;
  });
});

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight); captureTarget.setSize(innerWidth, innerHeight); env.resize();
});
