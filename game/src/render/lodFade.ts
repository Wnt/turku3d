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
/** Twins compiled during preparation (precompileDissolve) for at least one draw layout. */
const ready = new WeakSet<THREE.Material>();
/** Compiled draws by keeper key: a twin's programs and pipelines for one geometry layout and object kind.
 *  r186 frees a build once no render object uses it (NodeManager/Pipelines usedTimes), so a compiled twin keeps one
 *  detached keeper mesh with a few-vertex stand-in geometry of the same layout: the first live dissolve then finds its
 *  program and pipeline cached instead of building them inside a frame. Keepers go with their base material. */
const keepers = new Map<string, THREE.Mesh>();
const keepersOf = new WeakMap<THREE.Material, Set<string>>();
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
    material.addEventListener('dispose', () => {
      for (const key of keepersOf.get(material) ?? []) {
        const keeper = keepers.get(key);
        keepers.delete(key);
        if (keeper) { keeper.dispatchEvent({ type: 'dispose' } as never); keeper.geometry.dispose(); }
      }
      keepersOf.delete(material);
      for (const twin of list!) twin?.dispose();
      twins.delete(material);
    });
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
const materialsOf = (mesh: THREE.Mesh) => Array.isArray(mesh.material) ? mesh.material : [mesh.material];
const nodeMaterial = (material: THREE.Material) => (material as THREE.NodeMaterial).isNodeMaterial === true;
/** Instanced draws that share one program across meshes in every pass (render/instancePrograms.ts, still instances):
 *  a keeper can hold their twin builds. Other InstancedMeshes key builds by their own uuid and never dissolve. */
const sharedStill = (mesh: THREE.Mesh) => mesh.userData.sharedInstanceProgram === true && mesh.userData.staticInstances === true;

/** Meshes of `root` that swap to twins while it dissolves: main-view draws with a node material. Shadow-only batches
 *  and non-node materials keep drawing as they are. */
function dissolvingMeshes(root: THREE.Object3D) {
  const meshes: THREE.Mesh[] = [];
  root.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh || mesh.userData.shadowBatch || mesh.userData.shadowProxy) return;
    if ((mesh as THREE.InstancedMesh).isInstancedMesh && !sharedStill(mesh)) return;
    if (materialsOf(mesh).some(nodeMaterial)) meshes.push(mesh);
  });
  return meshes;
}

type Attribute = THREE.BufferAttribute | THREE.InterleavedBufferAttribute;
/** The parts of a draw that r186 keys builds and pipelines by besides the material (RenderObject.getCacheKey and the
 *  vertex buffer layout): attribute names, array types, item sizes, normalisation, interleaving, instancing. */
function layoutKey(mesh: THREE.Mesh) {
  const geometry = mesh.geometry, buffers: unknown[] = [];
  let key = '';
  for (const name of Object.keys(geometry.attributes).sort()) {
    const a = geometry.attributes[name] as Attribute & { gpuType?: number; meshPerAttribute?: number };
    const array = (a as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute ? (a as THREE.InterleavedBufferAttribute).data.array : a.array;
    key += `${name}:${array.constructor.name}:${a.itemSize}:${a.normalized ? 'n' : ''}:${a.gpuType ?? ''}:${a.meshPerAttribute ?? ''}`;
    if ((a as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute) {
      const data = (a as THREE.InterleavedBufferAttribute).data;
      if (!buffers.includes(data)) buffers.push(data);
      key += `@${buffers.indexOf(data)}/${data.stride}/${(a as THREE.InterleavedBufferAttribute).offset}`;
    }
    key += ';';
  }
  if (Object.keys(geometry.morphAttributes).length) key += `morph:${mesh.uuid};`;
  const instanced = mesh as unknown as THREE.InstancedMesh;
  if (instanced.isInstancedMesh) key += `inst:${instanced.instanceMatrix.usage}:${instanced.instanceColor?.usage ?? '-'};`;
  return `${key}|${mesh.receiveShadow}|${mesh.layers.mask}|${geometry.index ? 'i' : ''}`;
}
const keeperKey = (twin: THREE.Material, mesh: THREE.Mesh) => `${twin.uuid}|${layoutKey(mesh)}`;

/** A geometry of three vertices with exactly `source`'s attribute layout (types, item sizes, interleaving). */
function standIn(source: THREE.BufferGeometry) {
  const geometry = new THREE.BufferGeometry(), buffers = new Map<THREE.InterleavedBuffer, THREE.InterleavedBuffer>();
  const typed = (array: ArrayLike<number>, length: number) =>
    new (array.constructor as new (n: number) => THREE.TypedArray)(length);
  for (const [name, value] of Object.entries(source.attributes)) {
    const a = value as Attribute & { gpuType?: number };
    let copy: Attribute;
    if ((a as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute) {
      const ia = a as THREE.InterleavedBufferAttribute, data = ia.data;
      let buffer = buffers.get(data);
      if (!buffer) {
        const instancedData = data as THREE.InstancedInterleavedBuffer & { isInstancedInterleavedBuffer?: boolean };
        buffer = instancedData.isInstancedInterleavedBuffer
          ? new THREE.InstancedInterleavedBuffer(typed(data.array, data.stride * 3), data.stride, instancedData.meshPerAttribute)
          : new THREE.InterleavedBuffer(typed(data.array, data.stride * 3), data.stride);
        buffer.setUsage(data.usage);
        buffers.set(data, buffer);
      }
      copy = new THREE.InterleavedBufferAttribute(buffer, ia.itemSize, ia.offset, ia.normalized);
    } else {
      const ba = a as THREE.BufferAttribute & { meshPerAttribute?: number; isInstancedBufferAttribute?: boolean };
      copy = ba.isInstancedBufferAttribute
        ? new THREE.InstancedBufferAttribute(typed(ba.array, ba.itemSize * 3), ba.itemSize, ba.normalized, ba.meshPerAttribute)
        : new THREE.BufferAttribute(typed(ba.array, ba.itemSize * 3), ba.itemSize, ba.normalized);
      (copy as THREE.BufferAttribute).setUsage(ba.usage);
      if (ba.gpuType !== undefined) (copy as THREE.BufferAttribute & { gpuType?: number }).gpuType = ba.gpuType;
    }
    geometry.setAttribute(name, copy);
  }
  if (source.index) geometry.setIndex(new THREE.BufferAttribute(typed(source.index.array, 3), 1));
  geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1);
  geometry.boundingBox = new THREE.Box3(new THREE.Vector3(-1, -1, -1), new THREE.Vector3(1, 1, 1));
  return geometry;
}

/** The keeper draw of `twin` for `mesh`'s layout: a detached mesh (or shared still InstancedMesh) on its layers. */
function keeperFor(mesh: THREE.Mesh, twin: THREE.Material | THREE.Material[]) {
  const geometry = standIn(mesh.geometry);
  const source = mesh as unknown as THREE.InstancedMesh;
  let keeper: THREE.Mesh;
  if (source.isInstancedMesh) {
    const instanced = new THREE.InstancedMesh(geometry, twin, 1);
    instanced.instanceMatrix.setUsage(source.instanceMatrix.usage);
    if (source.instanceColor) {
      instanced.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(3), 3);
      instanced.instanceColor.setUsage(source.instanceColor.usage);
    }
    instanced.userData = { sharedInstanceProgram: true, staticInstances: true };
    keeper = instanced;
  } else keeper = new THREE.Mesh(geometry, twin);
  keeper.name = `${mesh.name}:dissolve-keeper`;
  for (const [key, value] of Object.entries(mesh.userData)) if (typeof value !== 'object' || value === null) keeper.userData[key] = value;
  keeper.layers.mask = mesh.layers.mask;
  // receiveShadow is part of r186's render-object cache key: match the live mesh, which casts nothing while it fades.
  keeper.receiveShadow = mesh.receiveShadow; keeper.castShadow = false;
  keeper.matrixAutoUpdate = false; keeper.frustumCulled = false;
  return keeper;
}

/** Whether every dissolving draw of `mesh` has a compiled keeper for `tier`. */
function meshReady(mesh: THREE.Mesh, tier: number) {
  const list = materialsOf(mesh);
  return list.every(material => !nodeMaterial(material) || keepers.has(keeperKey(dissolveTwin(material, tier), mesh)));
}

/** Compile the dissolve twins of every mesh under `roots` for `tiers` through `compile` (the game's own preparation
 *  path: post.prepare). The twins go onto detached proxy meshes sharing each mesh's geometry, layers and world
 *  transform, never onto the live model, which may already draw (post-menu backfill). Programs and pipelines are
 *  shared by material, geometry layout and pass, so a later fade finds them ready: no dissolve ever builds a shader or
 *  pipeline inside a frame, and none waits on an async pipeline with nothing drawn. */
export async function precompileDissolve(roots: readonly THREE.Object3D[], tiers: readonly number[],
  compile: (root: THREE.Object3D) => void | Promise<unknown>) {
  for (const tier of tiers) for (const root of roots) {
    const batch = new THREE.Group(), pending: { mesh: THREE.Mesh; keeper: THREE.Mesh; bases: THREE.Material[]; twins: THREE.Material[] }[] = [];
    const seen = new Set<string>();
    for (const mesh of dissolvingMeshes(root)) {
      const bases = materialsOf(mesh).filter(nodeMaterial);
      const list = materialsOf(mesh).map(material => dissolveTwin(material, tier));
      const key = list.map(twin => twin.uuid).join(',') + '|' + layoutKey(mesh);
      if (seen.has(key) || bases.every(base => keepers.has(keeperKey(dissolveTwin(base, tier), mesh)))) continue;
      seen.add(key);
      const keeper = keeperFor(mesh, Array.isArray(mesh.material) ? list : list[0]);
      batch.add(keeper);
      pending.push({ mesh, keeper, bases, twins: list.filter(isDissolveTwin) });
    }
    if (!pending.length) continue;
    try {
      await compile(batch);
    } catch (error) {
      for (const { keeper } of pending) { keeper.dispatchEvent({ type: 'dispose' } as never); keeper.geometry.dispose(); }
      throw error;
    } finally { batch.clear(); }
    for (const { mesh, keeper, bases, twins: compiled } of pending) {
      for (const base of bases) {
        const own = keeperKey(dissolveTwin(base, tier), mesh);
        keepers.set(own, keeper);
        let set = keepersOf.get(base);
        if (!set) keepersOf.set(base, set = new Set());
        set.add(own);
      }
      for (const twin of compiled) ready.add(twin);
    }
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
    return dissolvingMeshes(root).every(mesh => meshReady(mesh, tier));
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
      // A dissolving draw casts nothing: its shadow program is not among the compiled keepers.
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
