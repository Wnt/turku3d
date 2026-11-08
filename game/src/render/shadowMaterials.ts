import * as THREE from 'three/webgpu';
import { NormalPrepass } from './normalPrepass';
import { placementVisibility } from '../world/placementVisibility';
import { ownNodeAttributes } from './nodeAttributes';
import { instancePrograms, shareInstancePrograms, type InstanceDraw } from './instancePrograms';
import { asyncFirstUsePipelines, trustResolvedPipelines } from './asyncPipelines';
import { asyncFirstUseBuilds, type DirectDraw } from './asyncNodeBuilds';
import { BuildLog } from './census';
import type { WaterReflection } from './reflections';
import { executeBundles, renderBundle, type BundleDrawHook } from './bundleReplay';
import { altitudeShadows } from './shadowAltitude';
import type { ReflectionScope } from './reflectionScope';
import { poolUniformBuffers } from './uniformPool';
import { poolGeometryBuffers } from './geometryPool';
import { staticRefresh } from './staticRefresh';
import { SLOW_MS_PER_CHAR, ShaderModuleBudget } from './shaderModules';

type ShadowMaterial = THREE.Material & { isShadowPassMaterial?: boolean };

/** Keep opaque and cutout shadow programs separate. r186 copies each caster's alphaTest into the override;
 * alternating zero/nonzero bumps that shared material's version and rehashes every caster every frame. */
export class ShadowMaterials {
  private masked = new WeakMap<THREE.Material, { material: ShadowMaterial; version: number }>();
  private shared: THREE.Material | null = null;
  /** r186 gives every light its own, identical shadow material (ShadowBaseNode: colorNode vec4(0,0,0,1), no
   * blending, no fog; the renderer derives everything else from each draw's source material). Its node ids are part
   * of the program key, so every caster built one program per cascade. All shadow passes draw with the first. */
  private canonical(override: THREE.Material) {
    if (!this.shared) {
      const shared = this.shared = override;
      shared.addEventListener('dispose', () => { if (this.shared === shared) this.shared = null; });
    }
    return this.shared;
  }
  select(source: THREE.Material, override: THREE.Material | null): THREE.Material | null {
    if (!override || !(override as ShadowMaterial).isShadowPassMaterial) return override;
    override = this.canonical(override);
    if (source.alphaTest <= 0) return override;
    let cached = this.masked.get(override);
    if (!cached) {
      cached = { material: override.clone(), version: override.version };
      this.masked.set(override, cached);
      const material = cached.material;
      override.addEventListener('dispose', () => { material.dispose(); this.masked.delete(override); });
    } else if (cached.version !== override.version) {
      cached.material.copy(override);
      cached.version = override.version;
    }
    cached.material.isShadowPassMaterial = true;
    return cached.material;
  }
}

/** What r186's shadow pass takes from each caster's material (Renderer._getShadowNodes). */
export interface ShadowNodes { version: number; colorNode: THREE.Node | null; depthNode: THREE.Node | null; positionNode: THREE.Node | null }
/** Whether a caster's shadow depends on its alpha: an alpha test or a mask discards, a cast-shadow node colours the
 *  transmitted shadow (off here: PCF, render/renderer.ts). Otherwise the shadow pass writes depth whatever the alpha. */
export function shadowReadsAlpha(material: THREE.Material) {
  const m = material as THREE.NodeMaterial;
  return m.alphaTest > 0 || m.castShadowNode?.isNode === true || m.maskShadowNode?.isNode === true || m.maskNode?.isNode === true;
}
const baseShadowNodes = (THREE.WebGPURenderer.prototype as unknown as { _getShadowNodes(material: THREE.Material): ShadowNodes })._getShadowNodes;

type Draw = Parameters<THREE.WebGPURenderer['renderObject']>;
export interface ShadowPass { override: THREE.Material; camera: THREE.Camera; target: THREE.RenderTarget | null;
  mrt: ReturnType<THREE.WebGPURenderer['getMRT']>; depth: number; context: THREE.WebGPURenderer['contextNode'] }
/** r186 accepts count === 0; WebGPU warns on every pass. Reject empty range intersections before encoding. */
export function hasDrawVertices(geometry: THREE.BufferGeometry, group: Draw[5]) {
  const range = geometry.drawRange;
  const first = Math.max(0, range.start, group?.start ?? 0);
  const last = Math.min(geometry.index?.count ?? geometry.attributes.position?.count ?? 0,
    range.start + range.count, group ? group.start + group.count : Infinity);
  return last > first;
}
const base = THREE.WebGPURenderer.prototype as unknown as { _renderScene(scene: THREE.Object3D, camera: THREE.Camera, useFrameBufferTarget?: boolean): unknown };
type Internals = { _isPreCompiling?: boolean; _currentRenderBundle: unknown };
const directDraw = (base as unknown as { _renderObjectDirect: DirectDraw })._renderObjectDirect;
export class GameRenderer extends THREE.WebGPURenderer {
  readonly normalPrepass = new NormalPrepass();
  /** Every node-program build, for `__render.census()`. */
  readonly builds = new BuildLog();
  private shadowMaterials = new ShadowMaterials();
  private opaqueShadows = new WeakMap<ShadowNodes, ShadowNodes>();
  private readonly shadowContext = this.contextNode;
  /** Each live cascade's shadow draw state, keyed by three's per-light override material: preparation builds tiles'
   * shadow programs ahead with it (render/post.ts) instead of each new caster building them inside a frame (147 of
   * 397 in-frame builds in Jonni's replay light-1713). Cascades share their programs (ShadowMaterials). */
  readonly shadowPasses = new Map<THREE.Material, ShadowPass>();
  constructor(parameters?: ConstructorParameters<typeof THREE.WebGPURenderer>[0]) {
    super(parameters);
    this.debug.onNodeBuilderCreated = (builder, draw) => {
      instancePrograms(builder, (draw as InstanceDraw).object);
      this.instanceSharing?.builder(builder, draw as InstanceDraw);
      // In-frame builds stall the frame; preparation builds run in budgeted tasks (render/census.ts).
      this.builds.record(this, draw as Parameters<BuildLog['record']>[1],
        this.liveFrame && !(this as unknown as { _isPreCompiling?: boolean })._isPreCompiling);
    };
  }
  private releaseAttributes?: () => void;
  private instanceSharing?: ReturnType<typeof shareInstancePrograms>;
  private asyncPipelines?: ReturnType<typeof asyncFirstUsePipelines>;
  private resolvedPipelines?: ReturnType<typeof trustResolvedPipelines>;
  /** Every first-use pipeline requested asynchronously so far is compiled. */
  pipelinesSettled() { return Promise.all([this.asyncPipelines?.settled(), this.asyncBuilds?.settled()]).then(() => {}); }
  /** First-use node programs of the live frame build after it instead of inside it (render/asyncNodeBuilds.ts). */
  asyncBuilds?: ReturnType<typeof asyncFirstUseBuilds>;
  /** Set by PostProcessing.render around the per-frame pipeline: first-use draws compile their pipeline asynchronously. */
  liveFrame = false;
  /** Uniform buffers sub-allocated from shared pages (render/uniformPool.ts); set before init. */
  poolUniforms = false;
  uniformPool: ReturnType<typeof poolUniformBuffers> = null;
  /** Small static vertex/index buffers sub-allocated from shared pages (render/geometryPool.ts); set before init. */
  poolGeometry = false;
  geometryPool: ReturnType<typeof poolGeometryBuffers> = null;
  /** Unchanged static draws skip r186's full per-frame refresh (render/staticRefresh.ts); set before init. */
  staticDraws = false;
  staticRefresh: ReturnType<typeof staticRefresh> | null = null;
  /** New shader modules per frame (render/shaderModules.ts): live first-use draws and streaming preparation. */
  shaderModules: ShaderModuleBudget | null = null;
  /** Pace new shader modules (render/shaderModules.ts) at this translation rate (ms per WGSL character); set before init. */
  paceShaderModules = true;
  shaderModuleRate = SLOW_MS_PER_CHAR;
  private releaseModules?: () => void;
  override async init(): Promise<this> {
    await super.init();
    const webgpu = (this.backend as { isWebGPUBackend?: boolean }).isWebGPUBackend === true;
    const device = (this.backend as { device?: { createShaderModule(descriptor: unknown): unknown } }).device;
    if (webgpu && device && this.paceShaderModules && !this.shaderModules) {
      this.shaderModules = new ShaderModuleBudget(this.shaderModuleRate);
      this.releaseModules = this.shaderModules.wrap(device);
    }
    if (this.poolUniforms && !this.uniformPool && webgpu)
      this.uniformPool = poolUniformBuffers(this.backend as unknown as Parameters<typeof poolUniformBuffers>[0], () => this.info.frame);
    if (this.poolGeometry && !this.geometryPool && webgpu)
      this.geometryPool = poolGeometryBuffers(this.backend as unknown as Parameters<typeof poolGeometryBuffers>[0], () => this.info.frame);
    if (this.staticDraws && !this.staticRefresh) this.staticRefresh = staticRefresh(this);
    this.releaseAttributes ??= ownNodeAttributes(this);
    this.instanceSharing ??= shareInstancePrograms(this);
    if (webgpu) this.resolvedPipelines ??= trustResolvedPipelines(this.backend as unknown as Parameters<typeof trustResolvedPipelines>[0]);
    this.asyncPipelines ??= asyncFirstUsePipelines((this as unknown as { _pipelines: Parameters<typeof asyncFirstUsePipelines>[0] })._pipelines,
      () => this.liveFrame && !(this as unknown as { _isPreCompiling?: boolean })._isPreCompiling, this.shaderModules);
    this.asyncBuilds ??= asyncFirstUseBuilds(this, () => this.liveFrame && !(this as unknown as { _isPreCompiling?: boolean })._isPreCompiling);
    return this;
  }
  _renderObjectDirect(object: THREE.Object3D, material: THREE.Material, scene: THREE.Scene, camera: THREE.Camera,
    lightsNode: unknown, group: unknown, clippingContext: unknown, passId?: string) {
    if (this.asyncBuilds && !this.asyncBuilds.ready(object, material, scene, camera, lightsNode, group, clippingContext, passId)) return;
    directDraw.call(this, object, material, scene, camera, lightsNode, group, clippingContext, passId);
  }
  /** Census hook: every replayed draw of a static render bundle (render/staticBundles.ts, render/census.ts). */
  bundleDraw: BundleDrawHook | null = null;
  /** r186 internals, overridden for static render bundles (render/bundleReplay.ts). */
  _renderBundle(bundle: Parameters<typeof renderBundle>[1], sceneRef: THREE.Scene, lightsNode: unknown) {
    renderBundle(this, bundle, sceneRef, lightsNode, this.bundleDraw);
  }
  _renderBundles(bundles: Parameters<typeof renderBundle>[1][], sceneRef: THREE.Scene, lightsNode: unknown) {
    for (const bundle of bundles) this._renderBundle(bundle, sceneRef, lightsNode);
    executeBundles(this);
  }
  /** Subtrees a river reflection render skips without changing it (render/reflectionScope.ts); set by main.ts. */
  reflectionScope: ReflectionScope | null = null;
  /** A nested render (shadow maps drawn from a receiver's updateBefore) never records into an outer bundle. */
  _renderScene(scene: THREE.Object3D, camera: THREE.Camera, useFrameBufferTarget = true) {
    const self = this as unknown as Internals, outer = self._currentRenderBundle;
    self._currentRenderBundle = null;
    const water = (camera.userData as { waterReflection?: WaterReflection }).waterReflection;
    const scope = water && (scene as THREE.Scene).isScene ? this.reflectionScope : null;
    scope?.begin(scene, camera, water!);
    try { return base._renderScene.call(this, scene, camera, useFrameBufferTarget); }
    finally { scope?.end(); self._currentRenderBundle = outer; }
  }
  override dispose() { this.releaseModules?.(); this.staticRefresh?.release(); this.geometryPool?.release(); this.uniformPool?.release(); this.asyncPipelines?.release(); this.resolvedPipelines?.release(); this.instanceSharing?.dispose(); this.releaseAttributes?.(); this.normalPrepass.dispose(); return super.dispose(); }
  /** r186 gives a textured or colour-graph caster's shadow its alpha through a node bound to that material
   *  (reference('map', 'texture', material).a, or its colorNode's alpha). Node ids are part of the program key, so each
   *  such material built its own shadow program, carrying its colour graph and a map sample per shadow fragment:
   *  every player-character part at the play switch, every landmark wall. Where nothing reads the alpha
   *  (shadowReadsAlpha), the caster keeps only its depth and position nodes and shares the plain shadow program. */
  _getShadowNodes(material: THREE.Material): ShadowNodes {
    const nodes = baseShadowNodes.call(this, material);
    if (nodes.colorNode === null || shadowReadsAlpha(material)) return nodes;
    let opaque = this.opaqueShadows.get(nodes);
    if (!opaque) this.opaqueShadows.set(nodes, opaque = { ...nodes, colorNode: null });
    return opaque;
  }
  override renderObject(object: Draw[0], scene: Draw[1], camera: Draw[2], geometry: Draw[3], material: Draw[4],
    group: Draw[5], lights: Draw[6], clipping: Draw[7] = null, pass: Draw[8] = null) {
    if (!hasDrawVertices(geometry, group)) return;
    const internals = this as unknown as Internals;
    const precompiling = internals._isPreCompiling === true;
    // Pooled per-tile vegetation parts are drawn by their city-wide pool (world/vegetationLod.ts); they still compile.
    if (object.userData.pooledVegetation === true && !precompiling) return;
    // A static bundle records its members whatever this frame's culling decides: its group culls per pass instead.
    const recording = internals._currentRenderBundle !== null;
    // River reflections draw only what can land on water (render/reflections.ts); preparation still compiles all.
    const water = (camera.userData as { waterReflection?: WaterReflection }).waterReflection;
    if (water && !precompiling && !recording
      && !water.coverage.reflectsObject(object, water.eye, water.plane.matrixWorld.elements[13])) return;
    const original = scene.overrideMaterial;
    // Worker occlusion skips the primary camera's beauty and normal-prepass draws together; shadows, reflections
    // and shader preparation keep their own visibility.
    if ((original === null || original.userData.normalPrepass === true) && !precompiling && !recording
      && placementVisibility.occluded(object, camera)) return;
    const context = this.contextNode;
    // Puddles read this per-draw receiver flag from the prepass, or from beauty's MRT when there is no prepass.
    if (!original) material.userData.weatherReceiver = material.userData.weatherWetness ? 1 : 0;
    // Depth draws do not sample beauty-pass GTAO. Keep their context stable across beauty and reflection passes.
    if ((original as ShadowMaterial | null)?.isShadowPassMaterial) {
      this.contextNode = this.shadowContext;
      const pass = this.shadowPasses.get(original!), target = this.getRenderTarget();
      // Refreshed when a cascade's map or camera is rebuilt, so preparation never binds a released target.
      if (!precompiling && (!pass || pass.target !== target || pass.camera !== camera)) {
        if (!pass) original!.addEventListener('dispose', () => this.shadowPasses.delete(original!));
        this.shadowPasses.set(original!, { override: original!, camera, target, mrt: this.getMRT(),
          depth: (this as unknown as { _callDepth: number })._callDepth, context: this.shadowContext });
      }
      // Above about 100 m small casters leave the sun's cascades (render/shadowAltitude.ts); preparation compiles all.
      if (!precompiling && !recording && altitudeShadows.skips(object)) { this.contextNode = context; return; }
    }
    scene.overrideMaterial = original?.userData.normalPrepass
      ? this.normalPrepass.select(material) : this.shadowMaterials.select(material, original);
    try { super.renderObject(object, scene, camera, geometry, material, group, lights, clipping, pass); }
    finally { scene.overrideMaterial = original; this.contextNode = context; }
  }
}
