import { compileModel, nextRenderDepth, isPrecompiling } from './compileModel';
import * as THREE from 'three/webgpu';
import { mrt, reflector, vec4, velocity } from 'three/tsl';
import { labelGpuPass } from './gpuPasses';
import { REFLECTION_LAYER, ReflectionShadows, ReflectionCadence, WaterCoverage, type WaterReflection } from './reflections';
import { QUALITY, type QualityTier, type RiverReflection } from './quality';
import type { Environment } from './environment';
import { riverSurface } from './riverSurface';
import { waterColour } from './waterAppearance';
import { WATER_SWITCHES } from './waterSwitches';
import { WATER_HISTORY, type WaterHistory } from './waterHistory';

const liveRivers = new Set<RiverMaterial>();
/** A preset switch in a running session (main.ts switchQuality): the tier whose reflection size and still-camera rate
 *  the planar rivers use from now on; the technique itself (planar, screen-space, sky) stays the one they were built with. */
let reflectionTier: QualityTier | null = null;
export function setReflectionTier(tier: QualityTier) {
  reflectionTier = tier;
  for (const river of liveRivers) river.applyReflectionTier();
}

/** A water mesh's mirror point and normal (local space, its first vertex), sampled once while its arrays exist and
 *  kept in userData: render arrays (normals) of uploaded tiles are released later. Non-finite samples fall back to the
 *  mesh origin and +Y. */
export function waterSurface(mesh: THREE.Mesh): { point: THREE.Vector3; normal: THREE.Vector3 } {
  let sample = mesh.userData.waterSurface as { point: number[]; normal: number[] } | undefined;
  if (!sample) {
    const p = mesh.geometry.getAttribute('position'), n = mesh.geometry.getAttribute('normal');
    const point = p && p.array.length >= 3 ? new THREE.Vector3().fromBufferAttribute(p, 0) : new THREE.Vector3();
    const normal = n && n.array.length >= 3 ? new THREE.Vector3().fromBufferAttribute(n, 0) : new THREE.Vector3(0, 1, 0);
    sample = { point: point.toArray(), normal: normal.toArray() };
    if (p && p.array.length >= 3 && n && n.array.length >= 3) mesh.userData.waterSurface = sample;
  }
  const point = new THREE.Vector3().fromArray(sample.point), normal = new THREE.Vector3().fromArray(sample.normal);
  if (![point.x, point.y, point.z].every(Number.isFinite)) point.set(0, 0, 0);
  if (![normal.x, normal.y, normal.z].every(Number.isFinite) || normal.lengthSq() < 1e-8) normal.set(0, 1, 0);
  return { point, normal: normal.normalize() };
}
let activeSurface = riverSurface;

/** How a reach reflects: the tier's way (`?water=planar|ssr|sky` for the A/B), the sky where the tier has no reflection
 *  (mobile, `?off=reflection`) and on sloped reaches (rapids, weirs: no single mirror plane, but a screen-space march
 *  needs none), and the sky while TRAA keeps no history for a march (fxaa, off). */
export function riverReflection(tier: QualityTier, sloped: boolean): RiverReflection {
  if (QUALITY[tier].reflectionScale <= 0) return 'sky';
  const wanted = WATER_SWITCHES.reflect ?? QUALITY[tier].riverReflection;
  if (wanted === 'ssr') return WATER_HISTORY.depth && WATER_HISTORY.color && WATER_HISTORY.camera ? 'ssr' : 'sky';
  return wanted === 'planar' && sloped ? 'sky' : wanted;
}

let riverMrt: THREE.MRTNode | null = null;

export class RiverMaterial {
  readonly material = new THREE.NodeMaterial();
  /** Where this reach's mirror plane is real water: the reflection pass draws only what can land on it. */
  readonly coverage = new WaterCoverage();
  private disposeSurface = () => {};
  private reflectionUV: ReturnType<typeof reflector>['uvNode'];
  private context: THREE.WebGPURenderer['contextNode'];
  private prepared = false;
  private bodyColour: THREE.Color;
  private reflectionDepth = 3;
  private reflection: ReturnType<typeof reflector> | null;
  /** The previous frame TRAA keeps, for the bank fade (W3); null keeps an opaque, depth-writing river. */
  private history: WaterHistory | null;
  private mode: RiverReflection;
  /** A sloped reach (stream, weir, rapids): its ripples flow downhill and steep parts foam (W4). */
  private sloped: boolean;

  private cadence: ReflectionCadence | null = null;
  private builtTier: QualityTier = 'medium';
  /** The planar pass's size and still-camera rate: the switched tier's where it draws a reflection, else the built one's. */
  applyReflectionTier() {
    if (!this.reflection || !this.cadence) return;
    const q = reflectionTier && QUALITY[reflectionTier].reflectionScale > 0 ? QUALITY[reflectionTier] : QUALITY[this.builtTier];
    this.reflection.reflector.resolutionScale = q.reflectionScale;
    this.cadence.slowFps = q.reflectionSlowFps;
  }

  constructor(mesh: THREE.Mesh, private env: Environment, tier: QualityTier, private deterministic: boolean, renderer: THREE.WebGPURenderer) {
    const rgb = waterColour(mesh.userData);
    this.bodyColour = rgb ? new THREE.Color().setRGB(...rgb, THREE.SRGBColorSpace) : new THREE.Color(0.075, 0.062, 0.035);
    this.sloped = mesh.userData.water_slope === true;
    this.mode = riverReflection(tier, this.sloped);
    const reflection = this.reflection = this.mode === 'planar' ? reflector({ bounces: false, samples: 0 }) : null;
    this.reflectionUV = reflection?.uvNode ?? null;
    // Reflections do not sample the main camera's screen-space GTAO context.
    const context = this.context = renderer.contextNode;
    if (reflection) {
      this.builtTier = tier;
      const reflector = reflection.reflector;
      const renderTarget = reflector.getRenderTarget.bind(reflector);
      reflector.getRenderTarget = camera => {
        const target = renderTarget(camera);
        labelGpuPass(target, 'reflection');
        return target;
      };
      const virtualCamera = reflector.getVirtualCamera.bind(reflector);
      reflector.getVirtualCamera = camera => {
        const reflected = virtualCamera(camera);
        reflected.layers.set(REFLECTION_LAYER);
        reflected.userData.waterReflection = { coverage: this.coverage, eye: camera, plane: reflection.target } satisfies WaterReflection;
        return reflected;
      };
      const update = reflector.updateBefore.bind(reflector);
      const shadows = new ReflectionShadows();
      const cadence = this.cadence = new ReflectionCadence(QUALITY[tier].reflectionSlowFps);
      this.applyReflectionTier();
      reflector.updateBefore = frame => {
        if (isPrecompiling(renderer)) return;
        this.reflectionDepth = nextRenderDepth(renderer);
        if (!deterministic && frame.camera && !cadence.shouldUpdate(frame.camera, performance.now(), reflector.hasOutput, reflector.forceUpdate)) return;
        const previous = renderer.contextNode;
        renderer.contextNode = context;
        shadows.pause(env.csm.lights);
        try { update(frame); }
        finally { shadows.restore(env.csm.lights); renderer.contextNode = previous; }
      };
      this.attach(mesh);
    }
    // The screen-space march must not meet the water itself in the stored depth: it implies the bank fade's depth path.
    this.history = (WATER_SWITCHES.depth || this.mode === 'ssr') && WATER_HISTORY.depth ? WATER_HISTORY : null;
    this.setSurface(activeSurface);
    liveRivers.add(this);
    // Opaque colour, but exclude the river from the opaque normal/velocity prepass and its recursive reflection.
    this.material.transparent = true;
    // W3: the depth buffer keeps the opaque bed and walls below the surface for next frame's bank fade; nothing drawn
    // later needs the water's depth (rain draws without a depth test, the light sprites are above the surface).
    if (this.history) this.material.depthWrite = false;
    // W7: TRAA reprojects river pixels with the water's own motion. Without it they carried whatever was below the
    // surface (mostly the sky box's rotation-only motion), and ripples smeared while the camera moved along the river.
    // One node for every reach: node identity is part of the program key (render/riverSurface.ts sharedSurfaces).
    if (WATER_SWITCHES.motion && WATER_HISTORY.beautyVelocity) this.material.mrtNode = riverMrt ??= mrt({ velocity: vec4(velocity as unknown as THREE.Node<'vec2'>, 0, 1) });
    this.material.name = `river-${this.mode}`;
  }

  /** This reach's measured water colour (the shared surface graphs read it per mesh, render/riverSurface.ts). */
  get body() { return this.bodyColour; }

  /** Rebuild the material graph in place; keep streamed geometry and the reflection target resident. */
  setSurface(factory: typeof riverSurface) {
    this.disposeSurface();
    if (this.reflection) this.reflection.uvNode = this.reflectionUV;
    this.disposeSurface = factory(this.material, this.env, this.deterministic, this.reflection, this.bodyColour, this.history,
      this.mode === 'ssr', this.sloped);
    this.material.needsUpdate = true;
  }

  /** Compile every eligible surface in this exact target/camera context before publishing streamed content. */
  async prepare(renderer: THREE.WebGPURenderer, model: THREE.Object3D, scene: THREE.Scene, camera: THREE.PerspectiveCamera) {
    if (!this.reflection) return;
    const reflected = this.reflection.reflector.getVirtualCamera(camera);
    const target = this.reflection.reflector.getRenderTarget(reflected);
    if (!this.prepared && model !== scene) await this.prepare(renderer, scene, scene, camera);
    await compileModel(renderer, model, scene, reflected as THREE.PerspectiveCamera, target, null, this.context, false, this.reflectionDepth);
    this.prepared = true;
  }

  /** Transfer the shared reflection plane when its original tile unloads. */
  attach(mesh: THREE.Mesh) {
    if (!this.reflection) return;
    const target = this.reflection.target;
    // The plane comes from the surface sample taken while the mesh's arrays existed (waterSurface). A transfer to
    // another reach mesh happens when a tile unloads, long after the uploaded normal arrays of resident tiles were
    // released (world/releaseArrays.ts): reading them gave a NaN normal, a NaN mirror and no reflections at all.
    const { point, normal } = waterSurface(mesh);
    target.position.copy(point);
    target.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
    target.matrixAutoUpdate = true; // never frozen with a tile (freezeModel): it moves between reaches
    mesh.add(target);
  }

  dispose() {
    if (this.reflection) {
      this.reflection.target.removeFromParent();
      for (const target of this.reflection.reflector.renderTargets.values()) target.dispose();
      this.reflection.reflector.renderTargets.clear();
      this.reflection.dispose();
    }
    liveRivers.delete(this);
    this.disposeSurface();
    this.material.dispose();
  }
}

if (import.meta.hot) import.meta.hot.accept('./riverSurface', next => {
  if (next) {
    activeSurface = next.riverSurface;
    for (const river of liveRivers) river.setSurface(activeSurface);
  }
});
