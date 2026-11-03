// Screen-door LOD dissolves for far-band representations (distant scenery cells, block LOD shells).
//
// A far-band model that hands over to, or takes over from, a nearer representation is drawn for a
// moment on top of it: a "dissolve twin" of its material carries a depth bias towards the camera and
// keeps only the pixels whose ordered-dither threshold lies below the object's coverage. Coverage
// ramps over a fixed time, so the far model appears or vanishes pixel by pixel instead of popping.
// The nearer model stays opaque underneath with its own unchanged programs: only the cheap far-band
// materials ever gain a discard, and only while a fade runs. The dither offset changes every frame,
// so TRAA resolves it into a smooth cross-fade (GTA V's LOD-pair dither, without touching HD shaders).
import * as THREE from 'three/webgpu';
import { bool, dot, float, floor, fract, frameId, screenCoordinate, uniform, vec2 } from 'three/tsl';

/** Seconds a far-band model takes to dissolve in or out. */
export const LOD_FADE_SECONDS = 0.6;
/** Depth-bias tiers of the twin, chosen by the fading model's camera distance. A bias, not a vertex pull:
 *  positionNode would move facade programs onto r186's deformed velocity path and past the 16 inter-stage varyings
 *  (render/rigidVelocity.ts). With the 0.1 m near plane one unit is about 0.6e-6 * d^2 metres, so a fixed bias
 *  would pull a 4 km node about 460 m forward. Per tier the pull stays a few metres to tens of metres, enough to
 *  sit in front of facade relief and each band's terrain error, and no more:
 *  -48 units to 700 m (0.3 m at 100 m, 2.6 m at 300 m, 14 m at 700 m), -8 to 1800 m (2.3-15 m),
 *  -2 beyond (4 m at 1.8 km, 19 m at 4 km, at the depth buffer's resolution there). */
export const LOD_FADE_BIAS = [{ until: 700, units: -48 }, { until: 1800, units: -8 }, { until: Infinity, units: -2 }] as const;
const LOD_FADE_SLOPE = -2;
/** Bias tier of a model `distance` metres from the camera. */
export const fadeTier = (distance: number) => LOD_FADE_BIAS.findIndex(tier => distance < tier.until);

/** Per-draw coverage; meshes outside a fade never carry the twin, so its value is 1 for them. */
const coverage = uniform(1).onObjectUpdate(({ object }) => (object?.userData.lodCoverage as number | undefined) ?? 1);

/** 4x4 ordered dither in [0, 1): 16 distinct thresholds, every 2x2 quad covered evenly. */
function bayer2(a: THREE.Node<'vec2'>) {
  const cell = floor(a);
  return fract(dot(cell, vec2(.5, cell.y.mul(.75))));
}
export function ditherThreshold() {
  const pixel = screenCoordinate.xy;
  const ordered = bayer2(pixel.mul(.5)).mul(.25).add(bayer2(pixel));
  // A golden-ratio offset per frame visits every threshold; TRAA averages the pattern into coverage.
  return fract(ordered.add(float(frameId).mod(1024).mul(.61803398875)));
}

/** One mask and one shadow mask for every twin: the screen-door test against the drawn object's own coverage, so a
 *  twin differs from its base only by this shared node and its bias tier. */
const KEEP = ditherThreshold().lessThan(coverage);
const WHOLE = bool(true);

const twins = new WeakMap<THREE.Material, THREE.Material[]>();
const twinSet = new WeakSet<THREE.Material>();
/** Twins whose programs and pipelines were compiled during preparation (precompileDissolve). */
const ready = new WeakSet<THREE.Material>();
/** Twins belong to their base material, never to a model that happens to hold one while it fades. */
export const isDissolveTwin = (material: THREE.Material) => twinSet.has(material);

/** The dissolve twin of a far-band node material for a bias tier; cached per material and disposed with it. */
export function dissolveTwin(material: THREE.Material, tier = 0): THREE.Material {
  let list = twins.get(material);
  const cached = list?.[tier];
  if (cached) return cached;
  const base = material as THREE.NodeMaterial;
  if (!base.isNodeMaterial) return material;
  const node = base.clone() as THREE.NodeMaterial;
  node.name = `${base.name}:dissolve${tier ? `-${tier}` : ''}`;
  node.maskNode = base.maskNode ? bool(base.maskNode as THREE.Node<'bool'>).and(KEEP) : KEEP;
  // Fading meshes cast no shadow (LodFades.start); the nearer model draws, and casts, throughout the fade.
  node.maskShadowNode = base.maskShadowNode ?? base.maskNode ?? WHOLE;
  node.polygonOffset = true;
  node.polygonOffsetUnits = LOD_FADE_BIAS[tier].units; node.polygonOffsetFactor = LOD_FADE_SLOPE;
  if (!list) {
    list = []; twins.set(material, list);
    material.addEventListener('dispose', () => { for (const twin of list!) twin?.dispose(); twins.delete(material); });
  }
  list[tier] = node; twinSet.add(node);
  return node;
}

/** Whether `material`'s twin for `tier` was compiled during preparation. Non-node materials never dissolve. */
export function dissolveReady(material: THREE.Material, tier = 0) {
  const twin = twins.get(material)?.[tier];
  return !!twin && ready.has(twin);
}

/** Meshes a dissolve of `root` would swap (everything but instanced vegetation). */
function dissolvingMeshes(root: THREE.Object3D) {
  const meshes: THREE.Mesh[] = [];
  root.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (mesh.isMesh && !(mesh as THREE.InstancedMesh).isInstancedMesh) meshes.push(mesh);
  });
  return meshes;
}
const materialsOf = (mesh: THREE.Mesh) => Array.isArray(mesh.material) ? mesh.material : [mesh.material];

/** Compile the dissolve twins of every mesh under `roots` for `tiers` through `compile` (the game's own preparation
 *  path: post.prepare). The twins go onto detached proxy meshes sharing each mesh's geometry, layers and world
 *  transform, never onto the live model, which may already draw (post-menu backfill). Programs and pipelines are
 *  shared by material, geometry layout and pass, so a later fade finds them ready: no dissolve ever builds a shader or
 *  pipeline inside a frame, and none waits on an async pipeline with nothing drawn. */
export async function precompileDissolve(roots: readonly THREE.Object3D[], tiers: readonly number[],
  compile: (root: THREE.Object3D) => void | Promise<unknown>) {
  for (const tier of tiers) for (const root of roots) {
    const proxies = new THREE.Group(), compiled: THREE.Material[] = [];
    root.updateWorldMatrix(true, true);
    for (const mesh of dissolvingMeshes(root)) {
      const list = materialsOf(mesh).map(material => dissolveTwin(material, tier));
      if (list.every(twin => !isDissolveTwin(twin) || ready.has(twin))) continue;
      const proxy = new THREE.Mesh(mesh.geometry, Array.isArray(mesh.material) ? list : list[0]);
      proxy.name = mesh.name; proxy.layers.mask = mesh.layers.mask; proxy.userData = mesh.userData;
      // receiveShadow is part of r186's render-object cache key: match the live mesh, which casts nothing while it fades.
      proxy.receiveShadow = mesh.receiveShadow; proxy.castShadow = false;
      proxy.matrix.copy(mesh.matrixWorld); proxy.matrixAutoUpdate = false; proxy.frustumCulled = false;
      proxies.add(proxy);
      compiled.push(...list.filter(isDissolveTwin));
    }
    if (!proxies.children.length) continue;
    try { await compile(proxies); for (const twin of compiled) ready.add(twin); }
    finally { for (const proxy of proxies.children) proxy.dispatchEvent({ type: 'dispose' } as never); proxies.clear(); }
  }
}

interface Fade {
  from: number; to: number; start: number; seconds: number;
  meshes: { mesh: THREE.Mesh; material: THREE.Material | THREE.Material[]; castShadow: boolean }[];
  done?: () => void;
  tier: number;
}

/** Drives coverage for dissolving roots. A root fades as a whole: every mesh below it shares one coverage. */
export class LodFades {
  private fades = new Map<THREE.Object3D, Fade>();
  private clock = 0;
  get size() { return this.fades.size; }

  has(root: THREE.Object3D) { return this.fades.has(root); }

  /** Current coverage of a fading root (1 when it is not fading). */
  coverage(root: THREE.Object3D) {
    const fade = this.fades.get(root);
    return fade ? this.value(fade) : 1;
  }

  /** Whether `root` can dissolve on `tier` now: every mesh's twin compiled during preparation. */
  canStart(root: THREE.Object3D, tier = 0) {
    return dissolvingMeshes(root).every(mesh => materialsOf(mesh).every(material => dissolveReady(material, tier)));
  }

  /** Start (or reverse from its current coverage) a dissolve of `root` towards `to`; `done` runs once it lands.
   *  Returns false, changing nothing, when a twin of `tier` is not compiled yet: the caller swaps at once instead.
   *  A running dissolve is always reversed on its own tier. */
  start(root: THREE.Object3D, from: number, to: number, done?: () => void, now = performance.now(), seconds = LOD_FADE_SECONDS,
    tier = 0): boolean {
    this.clock = now;
    const running = this.fades.get(root);
    if (running) {
      from = this.value(running);
      running.done = undefined;
      tier = running.tier;
      this.restore(root, running);
    } else if (!this.canStart(root, tier)) return false;
    if (from === to || seconds <= 0) { done?.(); return true; }
    const meshes: Fade['meshes'] = [];
    for (const mesh of dissolvingMeshes(root)) {
      meshes.push({ mesh, material: mesh.material, castShadow: mesh.castShadow });
      mesh.material = Array.isArray(mesh.material) ? mesh.material.map(m => dissolveTwin(m, tier)) : dissolveTwin(mesh.material, tier);
      mesh.castShadow = false;
      mesh.userData.lodCoverage = from;
    }
    this.fades.set(root, { from, to, start: this.clock, seconds, meshes, done, tier });
    return true;
  }

  /** Abort a dissolve and restore its materials, without running its completion. Call before disposing `root`. */
  cancel(root: THREE.Object3D) {
    const fade = this.fades.get(root);
    if (fade) this.restore(root, fade);
  }

  /** Abort every dissolve on `root` or below it (a model about to be disposed). */
  cancelWithin(root: THREE.Object3D) {
    for (const [fading, fade] of [...this.fades]) {
      for (let node: THREE.Object3D | null = fading; node; node = node.parent)
        if (node === root) { this.restore(fading, fade); break; }
    }
  }

  clear() { for (const [root, fade] of [...this.fades]) this.restore(root, fade); }

  /** Advance every dissolve to `now` (ms). Completions run after their materials are restored. */
  update(now = performance.now()) {
    this.clock = now;
    if (!this.fades.size) return;
    const finished: (() => void)[] = [];
    for (const [root, fade] of [...this.fades]) {
      const value = this.value(fade);
      if (value === fade.to) {
        this.restore(root, fade);
        if (fade.done) finished.push(fade.done);
        continue;
      }
      for (const { mesh } of fade.meshes) mesh.userData.lodCoverage = value;
    }
    for (const done of finished) done();
  }

  private value(fade: Fade) {
    const t = Math.min(1, Math.max(0, (this.clock - fade.start) / (fade.seconds * 1000)));
    return t >= 1 ? fade.to : fade.from + (fade.to - fade.from) * t;
  }

  private restore(root: THREE.Object3D, fade: Fade) {
    for (const { mesh, material, castShadow } of fade.meshes) { mesh.material = material; mesh.castShadow = castShadow; delete mesh.userData.lodCoverage; }
    this.fades.delete(root);
  }
}

/** The world's dissolves, advanced once per frame by the streaming update. */
export const lodFades = new LodFades();
