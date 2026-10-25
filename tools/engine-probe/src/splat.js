// Mix test: three.js WebGLRenderer + Spark gaussian splats + mesh + Rapier physics.
// ?webgpu=1 tries Spark under WebGPURenderer to record whether that works.
import * as THREE from 'three';
import { SparkRenderer, SplatMesh } from '@sparkjsdev/spark';
import RAPIER from '@dimforge/rapier3d-compat';
import { createSim, runScenario } from './sim.js';

const hud = document.getElementById('hud');
window.__probe = { done: false, errors: [] };
addEventListener('error', (e) => window.__probe.errors.push(String(e.message)));
try {
  await RAPIER.init();
  const sim = createSim(RAPIER);
  let renderer;
  if (new URLSearchParams(location.search).has('webgpu')) {
    const W = await import('three/webgpu');
    renderer = new W.WebGPURenderer({ antialias: false });
    await renderer.init();
  } else {
    renderer = new THREE.WebGLRenderer({ antialias: false });
  }
  renderer.setSize(innerWidth, innerHeight);
  document.body.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x9ec5e8);
  const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.05, 500);
  camera.position.set(-6, 5, 12); camera.lookAt(6, 0.5, 0);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 2));
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(sim.level.positions, 3));
  g.setAttribute('color', new THREE.BufferAttribute(sim.level.colors, 3));
  g.setIndex(new THREE.BufferAttribute(sim.level.indices, 1));
  g.computeVertexNormals();
  scene.add(new THREE.Mesh(g, new THREE.MeshStandardMaterial({ vertexColors: true, flatShading: true })));

  const spark = new SparkRenderer({ renderer });
  scene.add(spark);
  // procedural "hedge/tree" made of N splats standing on the sidewalk (stand-in for a lidar-derived splat object)
  const N = 200000;
  const c = new THREE.Vector3(), s = new THREE.Vector3(), q = new THREE.Quaternion(), col = new THREE.Color();
  const t0 = performance.now();
  const splats = new SplatMesh({
    constructSplats: (ps) => {
      for (let i = 0; i < N; i++) {
        const a = Math.random() * Math.PI * 2, r = Math.sqrt(Math.random()) * 1.6, h = Math.random() * 3.5;
        c.set(2.5 + Math.cos(a) * r * (1 - h / 5), 0.12 + h, -5.5 + Math.sin(a) * r * (1 - h / 5));
        s.setScalar(0.02 + Math.random() * 0.03);
        col.setHSL(0.28 + Math.random() * 0.08, 0.6, 0.2 + Math.random() * 0.25);
        ps.pushSplat(c, s, q, 0.9, col);
      }
    },
  });
  scene.add(splats);
  await splats.initialized;
  const buildMs = performance.now() - t0;
  const res = runScenario(sim, 14, 30);
  let frames = 0; const r0 = performance.now();
  await new Promise((done) => { const tick = () => { renderer.render(scene, camera); ++frames < 60 ? requestAnimationFrame(tick) : done(); }; requestAnimationFrame(tick); });
  Object.assign(window.__probe, { renderer: renderer.isWebGPURenderer ? 'WebGPURenderer' : 'WebGLRenderer', splats: N, splatBuildMs: Math.round(buildMs), renderMsPerFrame: +((performance.now() - r0) / frames).toFixed(2), verdict: res.verdict, done: true });
  hud.textContent = JSON.stringify({ ...window.__probe, verdict: undefined });
} catch (e) {
  window.__probe.errors.push('caught: ' + (e && e.message));
  window.__probe.done = true;
  hud.textContent = 'ERROR ' + e.message;
}
