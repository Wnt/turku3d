// Browser probe: three.js WebGPURenderer (auto-falls back to WebGL2 backend)
// + Rapier WASM. ?auto=1 runs the scripted scenario deterministically and
// exposes results on window.__probe for the headless test.
import * as THREE from 'three/webgpu';
import RAPIER from '@dimforge/rapier3d-compat';
import { createSim, runScenario, DT } from './sim.js';

const params = new URLSearchParams(location.search);
const AUTO = params.has('auto');
const FORCE_WEBGL = params.has('webgl');
const hud = document.getElementById('hud');
const log = (...a) => {
  console.log('[probe]', ...a);
  hud.textContent = a.join(' ');
};

window.__probe = { done: false, errors: [] };
window.addEventListener('error', (e) => window.__probe.errors.push(String(e.message)));

await RAPIER.init();
const sim = createSim(RAPIER);

// ---------------- renderer ----------------
const renderer = new THREE.WebGPURenderer({ antialias: true, forceWebGL: FORCE_WEBGL });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
document.body.appendChild(renderer.domElement);
await renderer.init();
const backend = renderer.backend.isWebGPUBackend ? 'WebGPU' : 'WebGL2';
window.__probe.backend = backend;
window.__probe.rapier = RAPIER.version();
window.__probe.three = THREE.REVISION;
try {
  const ad = navigator.gpu && (await navigator.gpu.requestAdapter());
  const info = ad && (ad.info || (ad.requestAdapterInfo && (await ad.requestAdapterInfo())));
  window.__probe.adapter = info ? `${info.vendor} ${info.architecture} ${info.device} ${info.description}` : 'none';
} catch (e) { window.__probe.adapter = 'err ' + e.message; }
try {
  const gl = document.createElement('canvas').getContext('webgl2');
  const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
  window.__probe.glRenderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl ? 'webgl2 (masked)' : 'no webgl2';
} catch (e) { window.__probe.glRenderer = 'err'; }

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x9ec5e8);
scene.fog = new THREE.Fog(0x9ec5e8, 60, 200);
const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.05, 500);
scene.add(new THREE.HemisphereLight(0xdfefff, 0x404030, 1.2));
const sun = new THREE.DirectionalLight(0xffffff, 2.5);
sun.position.set(20, 40, 15);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
Object.assign(sun.shadow.camera, { left: -40, right: 40, top: 40, bottom: -40, near: 1, far: 120 });
scene.add(sun);

// ground mesh = same buffers as the collision trimesh
{
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(sim.level.positions, 3));
  g.setAttribute('color', new THREE.BufferAttribute(sim.level.colors, 3));
  g.setIndex(new THREE.BufferAttribute(sim.level.indices, 1));
  g.computeVertexNormals();
  const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.9, flatShading: true }));
  m.receiveShadow = m.castShadow = true;
  scene.add(m);
}

// walkers (capsules)
const walkerMeshes = sim.walkers.map((w, i) => {
  const m = new THREE.Mesh(
    new THREE.CapsuleGeometry(sim.R, sim.HH * 2, 6, 12),
    new THREE.MeshStandardMaterial({ color: [0xe0533d, 0x3d8be0, 0x4fbf5a][i] }),
  );
  m.castShadow = true;
  scene.add(m);
  return m;
});

// car
const carMesh = new THREE.Group();
{
  const body = new THREE.Mesh(
    new THREE.BoxGeometry(sim.halfExt.x * 2, sim.halfExt.y * 2, sim.halfExt.z * 2),
    new THREE.MeshStandardMaterial({ color: 0xf2c230, metalness: 0.3, roughness: 0.4 }),
  );
  body.castShadow = true;
  carMesh.add(body);
  scene.add(carMesh);
}
const wheelMeshes = sim.wheelPts.map(() => {
  const m = new THREE.Mesh(
    new THREE.CylinderGeometry(sim.WR, sim.WR, 0.22, 20).rotateX(Math.PI / 2),
    new THREE.MeshStandardMaterial({ color: 0x222222 }),
  );
  m.castShadow = true;
  scene.add(m);
  return m;
});

function syncMeshes() {
  sim.walkers.forEach((w, i) => {
    const t = w.body.translation();
    walkerMeshes[i].position.set(t.x, t.y, t.z);
  });
  const t = sim.chassis.translation(), r = sim.chassis.rotation();
  carMesh.position.set(t.x, t.y, t.z);
  carMesh.quaternion.set(r.x, r.y, r.z, r.w);
  for (let i = 0; i < 4; i++) {
    const p = sim.wheelPts[i];
    const sl = sim.car.wheelSuspensionLength(i) ?? 0.3;
    const local = new THREE.Vector3(p.x, p.y - sl, p.z).applyQuaternion(carMesh.quaternion).add(carMesh.position);
    wheelMeshes[i].position.copy(local);
    wheelMeshes[i].quaternion.copy(carMesh.quaternion);
    wheelMeshes[i].rotateY(sim.car.wheelSteering(i) ?? 0);
    wheelMeshes[i].rotateZ(-(sim.car.wheelRotation(i) ?? 0));
  }
  const f = walkerMeshes[0].position;
  camera.position.set(f.x - 7, f.y + 4, f.z + 9);
  camera.lookAt(f.x + 2, f.y, f.z - 1);
}

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

if (AUTO) {
  // deterministic: physics first (fixed dt), then render the final frame
  const t0 = performance.now();
  const res = runScenario(sim, 14, 15);
  const physMs = performance.now() - t0;
  syncMeshes();
  const r0 = performance.now();
  let frames = 0;
  // render 60 frames to get a rough GPU-side cost on this machine
  await new Promise((resolve) => {
    const tick = () => {
      renderer.render(scene, camera);
      if (++frames < 60) requestAnimationFrame(tick);
      else resolve();
    };
    requestAnimationFrame(tick);
  });
  Object.assign(window.__probe, res, {
    physMsPerFrame: +(physMs / res.samples.at(-1).frame).toFixed(3),
    renderMsPerFrame: +((performance.now() - r0) / frames).toFixed(2),
    done: true,
  });
  log(`${backend} done`, JSON.stringify(res.verdict));
} else {
  // interactive: free-run, fixed-step accumulator, simple keyboard car drive
  const keys = {};
  addEventListener('keydown', (e) => (keys[e.code] = true));
  addEventListener('keyup', (e) => (keys[e.code] = false));
  let acc = 0, last = performance.now();
  renderer.setAnimationLoop(() => {
    const now = performance.now();
    acc += Math.min(0.1, (now - last) / 1000);
    last = now;
    sim.carInput.throttle = keys.ArrowUp ? 1 : keys.ArrowDown ? -0.6 : 0;
    sim.carInput.steer = keys.ArrowLeft ? 0.5 : keys.ArrowRight ? -0.5 : 0;
    sim.carInput.brake = keys.Space ? 1 : 0;
    while (acc >= DT) {
      sim.step();
      acc -= DT;
    }
    syncMeshes();
    renderer.render(scene, camera);
    const s = sim.sample();
    hud.textContent = `${backend} | walker y=${s.walkers.stairs.feetY} | car x=${s.car.x} y=${s.car.y} v=${s.car.speed} | arrows drive car`;
  });
}
