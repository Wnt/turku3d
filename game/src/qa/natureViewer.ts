// Nature asset viewer (dev server: /nature.html), with the game's renderer, sky, clouds, shadows and post-processing.
//   ?view=asset&asset=tree_birch&lod=auto|0|1|2   one asset; LOD by camera distance (tier thresholds) or forced
//   ?view=lods&asset=tree_birch                    every LOD of one asset side by side
//   ?view=gallery                                  every asset at LOD 0
//   ?view=forest&count=800                         a synthetic tile vegetation model drawn by the game's VegetationLod
// Common: &time=HH:MM &wind=0..2 &wire=1 &quality=low|medium|high &shot=1 (no UI) &cam=x,y,z,tx,ty,tz
import * as THREE from 'three/webgpu';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { createRenderer } from '../render/renderer';
import { Environment } from '../render/environment';
import { DayClock } from '../render/dayClock';
import { PostProcessing } from '../render/post';
import { QUALITY, parseQuality } from '../render/quality';
import { loadNatureLibrary, type NatureAsset, type NatureLibrary } from '../world/nature/library';
import { VegetationLod } from '../world/vegetationLod';
import { hashId } from '../world/nature/species';

const query = new URLSearchParams(location.search);
const view = query.get('view') ?? 'asset';
const quality = parseQuality(query.get('quality'));
const hour = (() => { const [h, m] = (query.get('time') ?? '13:00').split(':').map(Number); return h + (m || 0) / 60; })();
if (query.get('shot') === '1') document.body.classList.add('shot');

const { renderer, backend } = await createRenderer('webgpu', document.body, { quality });
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.1, 6000);
const env = new Environment(scene, renderer, quality, false);
const clouds = env.loadClouds();
const clock = new DayClock(env, { kind: 'hour', hour }, false);
const post = new PostProcessing(renderer, scene, camera, quality, 'taa');
const library = await loadNatureLibrary(renderer, env, false);
await clouds.catch(() => {});
library.materials.windStrength.value = Number(query.get('wind') ?? 1);

const ground = new THREE.Mesh(new THREE.PlaneGeometry(3000, 3000).rotateX(-Math.PI / 2),
  new THREE.MeshStandardNodeMaterial({ color: 0x4f5b3a, roughness: 0.97 }));
ground.receiveShadow = true;
scene.add(ground);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

const panel = document.getElementById('panel')!, frame = document.getElementById('frame')!;
const placed: THREE.InstancedMesh[] = [];
const PAINT = [new THREE.Color(0.86, 0.85, 0.80), new THREE.Color(0.42, 0.09, 0.06), new THREE.Color(0.78, 0.62, 0.30), new THREE.Color(0.10, 0.22, 0.14)];

/** One asset LOD at a position, as instanced draws like the game (instance tint buffer included). */
function place(asset: NatureAsset, lod: number, x: number, z: number, rotation = 0, copies = 1, step = new THREE.Vector3()) {
  const parts = asset.lods[Math.min(lod, asset.lods.length - 1)].parts;
  for (const part of parts) {
    const mesh = new THREE.InstancedMesh(part.geometry, part.material, copies);
    for (let i = 0; i < copies; i++) {
      mesh.setMatrixAt(i, new THREE.Matrix4().compose(new THREE.Vector3(x, 0, z).addScaledVector(step, i), new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), rotation), new THREE.Vector3(1, 1, 1)));
      mesh.setColorAt(i, asset.kind === 'fence' && part.material === library.materials.wood ? PAINT[i % PAINT.length] : new THREE.Color(1, 1, 1));
    }
    mesh.castShadow = !(asset.kind === 'tree' && lod === asset.lods.length - 1);
    mesh.receiveShadow = true;
    mesh.userData = { asset: asset.name, lod };
    mesh.frustumCulled = false;
    scene.add(mesh); placed.push(mesh);
  }
}
function clear() { for (const m of placed.splice(0)) { scene.remove(m); m.dispose(); } }

/** Ground cover around the subject: grass clumps, meadow and rosettes with the leaf material. */
function meadow(radius: number, count: number) {
  const kinds = ['grass_clump', 'grass_wide', 'grass_meadow', 'groundcover_rosette'].map(n => library.asset(n));
  const matrix = new THREE.Matrix4(), rng = mulberry(7);
  for (const [k, asset] of kinds.entries()) {
    const n = Math.round(count * [0.55, 0.25, 0.12, 0.08][k]);
    for (const part of asset.lods[0].parts) {
      const mesh = new THREE.InstancedMesh(part.geometry, part.material, n);
      const r2 = mulberry(11 + k);
      for (let i = 0; i < n; i++) {
        const a = r2() * Math.PI * 2, d = Math.sqrt(r2()) * radius, s = 0.7 + r2() * 0.6;
        matrix.compose(new THREE.Vector3(Math.cos(a) * d, 0, Math.sin(a) * d), new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), r2() * 6.28), new THREE.Vector3(s, s, s));
        mesh.setMatrixAt(i, matrix);
        const b = 0.8 + r2() * 0.35; mesh.setColorAt(i, new THREE.Color(b, b, b * (0.9 + rng() * 0.15)));
      }
      mesh.receiveShadow = true; mesh.castShadow = false;
      scene.add(mesh); placed.push(mesh);
    }
  }
}

function mulberry(seed: number) {
  let a = seed >>> 0;
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

/** A synthetic tile with the generator's vegetation contract (variants, object ids, measured size per instance). */
function forest(lib: NatureLibrary, count: number) {
  const model = new THREE.Group(), rng = mulberry(3), box = new THREE.BoxGeometry(1, 1, 1);
  const variants = { tree_default_fall: 0.45, tree_detailed_fall: 0.3, tree_oak_fall: 0.08, tree_pineDefaultA: 0.17 } as Record<string, number>;
  for (const [variant, share] of Object.entries(variants)) {
    const n = Math.round(count * share), ids: string[] = [];
    const mesh = new THREE.InstancedMesh(box, new THREE.MeshStandardMaterial(), n);
    for (let i = 0; i < n; i++) {
      const x = (rng() - 0.5) * 420, z = -20 - rng() * 420, h = variant.includes('detailed') ? 13 + rng() * 6 : 6 + rng() * 7;
      mesh.setMatrixAt(i, new THREE.Matrix4().compose(new THREE.Vector3(x, 0, z), new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), rng() * 3), new THREE.Vector3(h * 0.3, h, h * 0.3)));
      ids.push(`osm:${hashId(`${variant}${i}`)}`);
    }
    mesh.userData = { layer: 'vegetation', variant, material_key: 'tree_foliage', instance_object_ids: ids };
    model.add(mesh);
  }
  const bushes = new THREE.InstancedMesh(box, new THREE.MeshStandardMaterial(), Math.round(count * 0.2)), bushIds: string[] = [];
  for (let i = 0; i < bushes.count; i++) {
    bushes.setMatrixAt(i, new THREE.Matrix4().compose(new THREE.Vector3((rng() - 0.5) * 300, 0, -10 - rng() * 300), new THREE.Quaternion(), new THREE.Vector3(1.1, 0.8 + rng() * 0.8, 1.1)));
    bushIds.push(`bush:${i}`);
  }
  bushes.userData = { layer: 'vegetation', variant: 'tree_default_fall', material_key: 'tree_foliage', instance_object_ids: bushIds };
  model.add(bushes);
  const vegetation = new VegetationLod(quality, lib);
  scene.add(model);
  vegetation.add(model);
  return vegetation;
}

const assets = [...library.assets.values()];
const subject = library.assets.get(query.get('asset') ?? 'tree_birch') ?? assets[0];
const lodParam = query.get('lod') ?? 'auto';
let vegetation: VegetationLod | null = null;
const target = new THREE.Vector3();

function build() {
  clear();
  if (view === 'forest') {
    vegetation = forest(library, Number(query.get('count') ?? 800));
    camera.position.set(0, 1.7, 0); target.set(0, 4, -60);
  } else if (view === 'gallery') {
    let x = 0;
    for (const asset of assets) {
      const w = asset.kind === 'fence' ? 10 : Math.max(2, asset.radius * 2.2);
      place(asset, 0, x + w / 2, 0, 0, asset.kind === 'fence' ? 5 : 1, new THREE.Vector3(2, 0, 0));
      x += w + 1;
    }
    target.set(x / 2, 3, 0); camera.position.set(x / 2, 12, x * 0.7);
  } else if (view === 'lods') {
    const w = subject.kind === 'fence' ? 11 : subject.radius * 2.4;
    subject.lods.forEach((_, k) => place(subject, k, (k - (subject.lods.length - 1) / 2) * w, 0, 0, subject.kind === 'fence' ? 5 : 1, new THREE.Vector3(2, 0, 0)));
    target.set(0, subject.height * 0.45, 0); camera.position.set(0, subject.height * 0.5, Math.max(8, w * subject.lods.length * 0.9));
    meadow(Math.max(14, w * 2), 1400);
  } else {
    place(subject, lodParam === 'auto' ? 0 : Number(lodParam), 0, 0, 0, subject.kind === 'fence' ? 5 : 1, new THREE.Vector3(2, 0, 0));
    target.set(subject.kind === 'fence' ? 4 : 0, subject.height * 0.45, 0);
    camera.position.set(subject.radius * 1.6 + 4, Math.max(1.7, subject.height * 0.35), subject.radius * 2.4 + 6);
    meadow(26, 1800);
  }
  const cam = query.get('cam')?.split(',').map(Number);
  if (cam?.length === 6) { camera.position.set(cam[0], cam[1], cam[2]); target.set(cam[3], cam[4], cam[5]); }
  controls.target.copy(target); controls.update();
  renderPanel();
}

/** Auto LOD in single-asset view: the game's tier distances (vegetation.near / mid). */
function autoLod() {
  if (view !== 'asset' || lodParam !== 'auto') return;
  const q = QUALITY[quality].vegetation, d = camera.position.distanceTo(new THREE.Vector3(0, subject.height / 2, 0));
  const want = Math.min(subject.lods.length - 1, d < q.near ? 0 : d < q.mid ? 1 : 2);
  const current = placed.find(m => m.userData.asset === subject.name)?.userData.lod;
  if (current !== want) {
    for (const m of placed.filter(m => m.userData.asset === subject.name)) { scene.remove(m); m.dispose(); placed.splice(placed.indexOf(m), 1); }
    place(subject, want, 0, 0, 0, subject.kind === 'fence' ? 5 : 1, new THREE.Vector3(2, 0, 0));
  }
}

function renderPanel() {
  const manifest = library.manifest;
  const rows = subject.lods.map((lod, k) => `<tr><td>LOD${k}</td><td>${lod.triangles.toLocaleString()}</td><td>${manifest?.assets[subject.name]?.lods[k]?.vertices.toLocaleString() ?? '-'}</td><td>${lod.parts.length}</td><td class="muted">${lod.parts.map(p => p.material.name.replace('nature-', '')).join(', ')}</td></tr>`).join('');
  const textures = Object.entries(manifest?.textures ?? {}).map(([name, t]) => `<tr><td>${name.replace('nature_', '')}</td><td>${t.width}x${t.height}</td><td>${(t.bytes / 1024).toFixed(0)} KiB</td><td class="muted">${t.format}</td></tr>`).join('');
  panel.innerHTML = `<h1>Turku3D nature assets</h1>
    <label>View <select id="v">${['asset', 'lods', 'gallery', 'forest'].map(v => `<option ${v === view ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
    <label>Asset <select id="a">${assets.map(a => `<option value="${a.name}" ${a === subject ? 'selected' : ''}>${a.name} (${a.kind})</option>`).join('')}</select></label>
    <label>Hour <input id="h" type="range" min="0" max="24" step="0.25" value="${hour}"></label>
    <label>Wind <input id="w" type="range" min="0" max="2" step="0.05" value="${library.materials.windStrength.value}"></label>
    <label><input id="wire" type="checkbox" style="width:auto"> wireframe</label>
    <p><b>${subject.name}</b> <span class="muted">${subject.species ?? ''} · ${subject.height.toFixed(1)} m tall · radius ${subject.radius.toFixed(1)} m${subject.module ? ` · ${subject.module} m module` : ''}</span></p>
    <table><tr><th>LOD</th><th>tris</th><th>verts</th><th>draws</th><th></th></tr>${rows}</table>
    <p class="muted">Shared by every asset (one material each: bark, leaves, impostor, wood, metal, granite):</p>
    <table>${textures}</table>
    <p class="muted">nature.glb ${manifest ? (manifest.glb.bytes / 1048576).toFixed(2) : '?'} MiB · ${backend}</p>`;
  const go = (params: Record<string, string>) => { for (const [k, v] of Object.entries(params)) query.set(k, v); location.search = query.toString(); };
  panel.querySelector<HTMLSelectElement>('#v')!.onchange = e => go({ view: (e.target as HTMLSelectElement).value });
  panel.querySelector<HTMLSelectElement>('#a')!.onchange = e => go({ asset: (e.target as HTMLSelectElement).value });
  panel.querySelector<HTMLInputElement>('#h')!.oninput = e => { clock.setHour(Number((e.target as HTMLInputElement).value)); };
  panel.querySelector<HTMLInputElement>('#w')!.oninput = e => { library.materials.windStrength.value = Number((e.target as HTMLInputElement).value); };
  panel.querySelector<HTMLInputElement>('#wire')!.onchange = e => setWire((e.target as HTMLInputElement).checked);
}
function setWire(on: boolean) {
  for (const m of [library.materials.bark, library.materials.leaves, library.materials.impostor, library.materials.wood, library.materials.metal, library.materials.granite]) {
    m.wireframe = on; m.needsUpdate = true;
  }
}

build();
if (query.get('wire') === '1') setWire(true);
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight); env.resize();
});

let frames = 0, windowStart = performance.now(), windowFrames = 0, fps = 0;
const stats = { frames: 0, fps: 0, calls: 0, triangles: 0 };
function loop() {
  controls.update();
  autoLod();
  env.follow(camera.position);
  env.updateIBL();
  env.updateShadows(false);
  post.grade(env.sample);
  vegetation?.update(camera);
  renderer.info.reset();
  post.render();
  frames++; windowFrames++;
  const now = performance.now();
  if (now - windowStart > 1000) { fps = windowFrames * 1000 / (now - windowStart); windowFrames = 0; windowStart = now; }
  const info = renderer.info.render;
  Object.assign(stats, { frames, fps, calls: info.drawCalls ?? info.calls, triangles: info.triangles });
  frame.textContent = `${fps.toFixed(0)} fps · ${quality}\n${stats.calls} draws\n${(stats.triangles / 1000).toFixed(0)}k triangles (all passes)\ncamera ${camera.position.toArray().map(v => v.toFixed(1)).join(', ')}`;
  requestAnimationFrame(loop);
}
renderer.info.autoReset = false;
requestAnimationFrame(loop);
Object.assign(window, { __viewer: { stats, library, camera, controls, clock, scene, setWire } });
