// Render-budget probe: WebGPURenderer + CSM shadows + GTAO + TRAA + bloom on a
// city-sized load (1M-tri terrain + N PBR "buildings"). ?fx=0 disables post. ?webgl forces WebGL2 backend.
import * as THREE from 'three/webgpu';
import { pass, mrt, sample, screenUV, normalView, velocity, packNormalToRGB, unpackRGBToNormal, builtinAOContext } from 'three/tsl';
import { ao } from 'three/addons/tsl/display/GTAONode.js';
import { traa } from 'three/addons/tsl/display/TRAANode.js';
import { bloom } from 'three/addons/tsl/display/BloomNode.js';
import { CSMShadowNode } from 'three/addons/csm/CSMShadowNode.js';
import { SkyMesh } from 'three/addons/objects/SkyMesh.js';
import { buildBigGrid } from './level.js';

const P = new URLSearchParams(location.search);
const FX = P.get('fx') !== '0';
const NB = +(P.get('buildings') ?? 600);
const hud = document.getElementById('hud');
window.__probe = { done: false, errors: [] };
addEventListener('error', (e) => window.__probe.errors.push(String(e.message)));

const renderer = new THREE.WebGPURenderer({ antialias: !FX, forceWebGL: P.has('webgl'), trackTimestamp: true });
renderer.setPixelRatio(1);
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.AgXToneMapping;
renderer.toneMappingExposure = 0.3;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
document.body.appendChild(renderer.domElement);
await renderer.init();

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.1, 2000);
camera.position.set(-20, 6, -20);
camera.lookAt(60, 0, 60);

// physical sky + IBL from it
const sky = new SkyMesh();
sky.scale.setScalar(4000);
const sunDir = new THREE.Vector3().setFromSphericalCoords(1, THREE.MathUtils.degToRad(60), THREE.MathUtils.degToRad(200));
sky.sunPosition.value.copy(sunDir);
scene.add(sky);
const pm = new THREE.PMREMGenerator(renderer);
const envScene = new THREE.Scene(); envScene.add(sky.clone());
scene.environment = pm.fromScene(envScene).texture;

const sun = new THREE.DirectionalLight(0xfff2e0, 3);
sun.position.copy(sunDir).multiplyScalar(200);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.near = 1; sun.shadow.camera.far = 600;
sun.shadow.bias = -0.0003;
const csm = new CSMShadowNode(sun, { cascades: 4, maxFar: 300, mode: 'practical', lightMargin: 100 });
sun.shadow.shadowNode = csm;
scene.add(sun);

// 1000x1000 quads @0.25 m = 250 m tile, 2M tris
const g = buildBigGrid(1000, 0.25);
const tg = new THREE.BufferGeometry();
tg.setAttribute('position', new THREE.BufferAttribute(g.positions, 3));
tg.setIndex(new THREE.BufferAttribute(g.indices, 1));
tg.computeVertexNormals();
const terrain = new THREE.Mesh(tg, new THREE.MeshStandardMaterial({ color: 0x5a5a58, roughness: 0.85 }));
terrain.receiveShadow = true;
scene.add(terrain);

// buildings: InstancedMesh boxes with PBR
const bg = new THREE.BoxGeometry(1, 1, 1);
const bm = new THREE.MeshStandardMaterial({ color: 0xc8b8a0, roughness: 0.6, metalness: 0.0 });
const inst = new THREE.InstancedMesh(bg, bm, NB);
const m = new THREE.Matrix4(); let seed = 7; const r = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
for (let i = 0; i < NB; i++) {
  const w = 6 + r() * 14, d = 6 + r() * 14, h = 6 + r() * 24;
  m.compose(new THREE.Vector3(Math.floor(r() * 10) * 25 + 10, h / 2, Math.floor(r() * 10) * 25 + 10), new THREE.Quaternion(), new THREE.Vector3(w, h, d));
  inst.setMatrixAt(i, m);
}
inst.castShadow = inst.receiveShadow = true;
scene.add(inst);
// some glossy cars-ish spheres
const cars = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 32, 16), new THREE.MeshStandardMaterial({ color: 0x992222, roughness: 0.2, metalness: 0.8 }), 300);
for (let i = 0; i < 300; i++) { m.compose(new THREE.Vector3(r() * 250, 0.8, r() * 250), new THREE.Quaternion(), new THREE.Vector3(2.2, 0.8, 1)); cars.setMatrixAt(i, m); }
cars.castShadow = true; scene.add(cars);

let pipeline = null;
if (FX) {
  pipeline = new THREE.RenderPipeline(renderer);
  const pre = pass(scene, camera);
  pre.transparent = false;
  pre.setMRT(mrt({ output: packNormalToRGB(normalView), velocity }));
  pre.getTexture('output').type = THREE.UnsignedByteType;
  const preDepth = pre.getTextureNode('depth');
  const preNormal = sample((uv) => unpackRGBToNormal(pre.getTextureNode().sample(uv)));
  const aoPass = ao(preDepth, preNormal, camera);
  aoPass.resolutionScale = 0.5;
  const scenePass = pass(scene, camera);
  scenePass.contextNode = builtinAOContext(aoPass.getTextureNode().sample(screenUV).r);
  const aa = traa(scenePass, preDepth, pre.getTextureNode('velocity'), camera);
  pipeline.outputNode = aa.add(bloom(aa, 0.15, 0.4, 0.9));
}

const frameTimes = [];
let last = performance.now(), n = 0, t = 0, tStart = 0;
const gpuMs = [];
const WARM = 30, FRAMES = 240;
renderer.setAnimationLoop(() => {
  t += 1 / 60;
  camera.position.set(-30 + t * 2, 30, -10 + t * 1);
  camera.lookAt(125, 0, 125);
  if (n === WARM) tStart = performance.now();
  if (pipeline) pipeline.render(); else renderer.render(scene, camera);
  if (n > WARM && n % 10 === 0) renderer.resolveTimestampsAsync('render').then((ms) => { if (ms) gpuMs.push(ms); }).catch(() => {});
  const now = performance.now();
  if (n > WARM) frameTimes.push(now - last);
  last = now;
  if (++n === WARM + FRAMES) {
    renderer.setAnimationLoop(null);
    finish();
  }
});
async function finish() {
    if (renderer.backend.isWebGPUBackend) await renderer.backend.device.queue.onSubmittedWorkDone();
    else { const gl = renderer.backend.gl; gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(4)); }
    const throughputMs = (performance.now() - tStart) / FRAMES;
    frameTimes.sort((a, b) => a - b);
    const avg = frameTimes.reduce((a, b) => a + b, 0) / frameTimes.length;
    Object.assign(window.__probe, {
      backend: renderer.backend.isWebGPUBackend ? 'WebGPU' : 'WebGL2', fx: FX, buildings: NB,
      res: `${innerWidth}x${innerHeight}`, avgMs: +avg.toFixed(2), p50: +frameTimes[frameTimes.length >> 1].toFixed(2),
      p95: +frameTimes[Math.floor(frameTimes.length * 0.95)].toFixed(2), throughputMsPerFrame: +throughputMs.toFixed(2),
      gpuTimestampMs: gpuMs.length ? +(gpuMs.reduce((a, b) => a + b, 0) / gpuMs.length).toFixed(2) : null, drawCalls: renderer.info.render.drawCalls, tris: renderer.info.render.triangles, done: true,
    });
    hud.textContent = JSON.stringify(window.__probe);
}
