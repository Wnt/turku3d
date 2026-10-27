// Isolated material QA: no game feature switch and no writes to the streamed scene.
import * as THREE from 'three/webgpu';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { createGroundMaterial, GROUND_KEYS, loadGroundLibrary, loadGroundTile } from '../render/ground/material';
import type { GroundTileSpec } from '../render/ground/material';

const query = new URLSearchParams(location.search);
const before = query.get('capture') === 'before';
const backend = query.get('backend') ?? 'webgpu';
const view = Number(query.get('view') ?? 0);
const renderer = new THREE.WebGPURenderer({ forceWebGL: backend === 'webgl', antialias: true });
renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(1);
renderer.toneMapping = THREE.AgXToneMapping;
await renderer.init();
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xb8c5ce);
scene.add(new THREE.HemisphereLight(0xffffff, 0x9da3a9, 2.5));
const sun = new THREE.DirectionalLight(0xfff7eb, 2);
sun.position.set(-4, 8, 3); scene.add(sun);
const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, .05, 300);

function dataTexture(data: number[], width: number, height: number) {
  const t = new THREE.DataTexture(new Uint8Array(data), width, height, THREE.RGBAFormat);
  t.needsUpdate = true;
  return t;
}
function arrayTexture(kind: 'albedo' | 'normal' | 'orm') {
  const data = new Uint8Array(32 * 32 * 8 * 4);
  const colours = [[55, 58, 60], [100, 105, 110], [150, 140, 135], [130, 140, 150],
    [180, 175, 170], [70, 100, 35], [140, 120, 90], [105, 105, 110]];
  for (let layer = 0; layer < 8; layer++) {
    for (let i = 0; i < 32 * 32; i++) {
      const noise = ((i * 17 + i % 32 * 31) % 29) - 14;
      const rgb = kind === 'albedo' ? colours[layer].map(c => c + noise)
        : kind === 'normal' ? [128 + noise, 128, 250] : [240, 220, 0];
      data.set([...rgb, 255], (layer * 32 * 32 + i) * 4);
    }
  }
  const t = new THREE.DataArrayTexture(data, 32, 32, 8);
  t.format = THREE.RGBAFormat;
  t.colorSpace = kind === 'albedo' ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  t.needsUpdate = true;
  return t;
}

let bounds: number[] = [0, 0, 500, 500];
let eye: [number, number];
let heading: number;
const assetBase = query.get('assets');
if (assetBase) {
  const base = new URL(assetBase, location.href).href;
  const loader = new KTX2Loader().setTranscoderPath(`${import.meta.env.BASE_URL}basis/`).detectSupport(renderer);
  const library = await loadGroundLibrary(new URL('library/', base).href, loader);
  const poses = [
    { tile: 't0_0_1', x: 147.292, z: 271.906, yaw: 38.637 },
    { tile: 't0_0_0', x: 203.504, z: 241.290, yaw: 262.443 },
    { tile: 't0_0_1', x: 175.963, z: 257.297, yaw: 324.819 },
  ];
  const pose = poses[view];
  eye = [pose.x, pose.z]; heading = pose.yaw;
  for (const tileId of ['t0_0_0', 't0_0_1', 't0_1_0', 't0_1_1']) {
    const specUrl = new URL(`ground/${tileId}/material.json`, base).href;
    if (before) {
      const response = await fetch(specUrl);
      const spec: GroundTileSpec = await response.json();
      const map = await loader.loadAsync(new URL(`ground/${tileId}/ortho2022.ktx2`, base).href);
      map.offset.setScalar(spec.ortho_uv.offset);
      map.repeat.setScalar(spec.ortho_uv.scale);
      addGround(spec.bounds_xz, new THREE.MeshStandardMaterial({ map, roughness: .9 }));
    } else {
      const tile = await loadGroundTile(specUrl, loader, library);
      addGround(tile.spec.bounds_xz, tile.material);
    }
  }

} else {
  const spec: GroundTileSpec = { version: 1, material_keys: [...GROUND_KEYS], bounds_xz: [0, 0, 20, 20],
    ortho_uv: { offset: 0, scale: 1 }, splat_uv: { offset: 0, scale: 1 },
    textures: { ortho: { uri: '' }, splat0: { uri: '' }, splat1: { uri: '' } } };
  const lo: number[] = [], hi: number[] = [];
  for (let x = 0; x < 8; x++) {
    const w = Array.from({ length: 8 }, (_, i) => i === x ? 255 : 0);
    lo.push(...w.slice(0, 4)); hi.push(...w.slice(4));
  }
  const ortho = dataTexture([125, 128, 122, 255], 1, 1);
  ortho.colorSpace = THREE.SRGBColorSpace;
  const material = createGroundMaterial(spec, { ortho, splat0: dataTexture(lo, 8, 1), splat1: dataTexture(hi, 8, 1) },
    { albedo: arrayTexture('albedo'), normal: arrayTexture('normal'), orm: arrayTexture('orm') });
  bounds = spec.bounds_xz; eye = [10, 18]; heading = 0;
  addGround(bounds, material);
}
function addGround(bounds: number[], material: THREE.Material) {
  const [x0, z0, x1, z1] = bounds;
  const geometry = new THREE.PlaneGeometry(x1 - x0, z1 - z0, 1, 1);
  geometry.rotateX(-Math.PI / 2);
  geometry.translate((x0 + x1) / 2, 0, (z0 + z1) / 2);
  // Native ground UVs are E/N metres. Baseline ortho needs normalized x/z image UVs.
  const positions = geometry.getAttribute('position');
  const uvs = geometry.getAttribute('uv');
  for (let i = 0; i < uvs.count; i++) {
    const x = positions.getX(i), z = positions.getZ(i);
    if (before) uvs.setXY(i, (x - x0) / (x1 - x0), (z - z0) / (z1 - z0));
    else uvs.setXY(i, x, -z);
  }
  scene.add(new THREE.Mesh(geometry, material));
}
camera.position.set(eye[0], 1.7, eye[1]);
const yaw = THREE.MathUtils.degToRad(heading);
camera.lookAt(eye[0] + Math.sin(yaw) * 5, 0, eye[1] - Math.cos(yaw) * 5);
document.querySelector('#caption')!.textContent = `${before ? 'Before: 2022 ortho' : 'After: ground material'} · view ${view + 1} · 1.7 m eye · ${backend} · isolated flat ground preview${assetBase ? ' · Base map © City of Turku, CC BY 4.0' : ''}`;
await renderer.compileAsync(scene, camera);
for (let i = 0; i < 3; i++) await renderer.renderAsync(scene, camera);
const shader = await renderer.debug.getShaderAsync(scene, camera, scene.children.find(o => (o as THREE.Mesh).isMesh)!);
Object.assign(window, { groundShader: shader, groundQA: { ready: true, backend: renderer.backend.constructor.name,
  view, before, eye: camera.position.toArray(), bounds, calls: renderer.info.render.calls } });
