// Loads server-built GLB (EXT_meshopt_compression + KHR_texture_basisu) in WebGPURenderer,
// then builds a Rapier trimesh from the decoded geometry (render mesh == collision mesh path).
import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import RAPIER from '@dimforge/rapier3d-compat';
const P = new URLSearchParams(location.search);
const file = P.get('f') ?? 'level.etc1s.meshopt.glb';
window.__probe = { done: false, errors: [] };
try {
  const renderer = new THREE.WebGPURenderer({ forceWebGL: P.has('webgl') });
  renderer.setSize(innerWidth, innerHeight);
  document.body.appendChild(renderer.domElement);
  await renderer.init();
  await RAPIER.init();
  const ktx2 = new KTX2Loader().setTranscoderPath('basis/').detectSupport(renderer);
  const loader = new GLTFLoader().setKTX2Loader(ktx2).setMeshoptDecoder(MeshoptDecoder);
  const t0 = performance.now();
  const gltf = await loader.loadAsync('assets/' + file);
  const tLoad = performance.now() - t0;
  const scene = new THREE.Scene(); scene.add(gltf.scene); scene.background = new THREE.Color(0x88aacc);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x333333, 3));
  const cam = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.1, 1000); cam.position.set(-20, 30, -20); cam.lookAt(60, 0, 60);
  // collision from decoded (dequantized) geometry
  const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  let tris = 0; const t1 = performance.now(); let texFormat = null;
  gltf.scene.updateMatrixWorld(true);
  gltf.scene.traverse((o) => {
    if (!o.isMesh) return;
    // NB: KHR_mesh_quantization stores int16-normalized positions; dequantize to Float32 BEFORE
    // applying the node matrix (geometry.applyMatrix4 would write back into int16 and clamp).
    const g = o.geometry;
    const pos = g.attributes.position; const p = new Float32Array(pos.count * 3); const v = new THREE.Vector3();
    for (let i = 0; i < pos.count; i++) { v.fromBufferAttribute(pos, i).applyMatrix4(o.matrixWorld); p[3 * i] = v.x; p[3 * i + 1] = v.y; p[3 * i + 2] = v.z; }
    const idx = new Uint32Array(g.index.array);
    world.createCollider(RAPIER.ColliderDesc.trimesh(p, idx));
    tris += idx.length / 3;
    texFormat = o.material.map && (o.material.map.isCompressedTexture ? 'compressed fmt ' + o.material.map.format : 'uncompressed');
  });
  const tCol = performance.now() - t1;
  world.step();
  const hit = world.castRay(new RAPIER.Ray({ x: 7.0, y: 10, z: 0 }, { x: 0, y: -1, z: 0 }), 20, true);
  renderer.render(scene, cam);
  Object.assign(window.__probe, { file, backend: renderer.backend.isWebGPUBackend ? 'WebGPU' : 'WebGL2', loadMs: Math.round(tLoad), colliderBuildMs: Math.round(tCol), tris, texFormat,
    rayHitYAtStairs: hit ? +(10 - hit.timeOfImpact).toFixed(3) : null,
    rayHitYGrid: (() => { const h = world.castRay(new RAPIER.Ray({ x: 7, y: 10, z: 7 }, { x: 0, y: -1, z: 0 }), 20, true); return h ? +(10 - h.timeOfImpact).toFixed(3) : null; })(),
    bbox: new THREE.Box3().setFromObject(gltf.scene).min.toArray().map((v) => +v.toFixed(2)).concat(new THREE.Box3().setFromObject(gltf.scene).max.toArray().map((v) => +v.toFixed(2))),
    colliders: world.colliders.len(),
    done: true });
  document.getElementById('hud').textContent = JSON.stringify(window.__probe);
} catch (e) { window.__probe.errors.push(String(e && e.stack || e)); window.__probe.done = true; }
