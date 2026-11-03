import { compileModel, nextRenderDepth, isPrecompiling } from './compileModel';
import * as THREE from 'three/webgpu';
import { reflector } from 'three/tsl';
import { labelGpuPass } from './gpuPasses';
import { REFLECTION_LAYER, ReflectionShadows, ReflectionCadence, WaterCoverage, type WaterReflection } from './reflections';
import { QUALITY, type QualityTier } from './quality';
import type { Environment } from './environment';
import { riverSurface } from './riverSurface';
import { waterColour } from './waterAppearance';

const liveRivers = new Set<RiverMaterial>();

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

  constructor(mesh: THREE.Mesh, private env: Environment, tier: QualityTier, private deterministic: boolean, renderer: THREE.WebGPURenderer) {
    const rgb = waterColour(mesh.userData);
    this.bodyColour = rgb ? new THREE.Color().setRGB(...rgb, THREE.SRGBColorSpace) : new THREE.Color(0.075, 0.062, 0.035);
    const scale = QUALITY[tier].reflectionScale;
    // Sloped reaches (rapids, weirs) have no single mirror plane; they shade from the sky.
    const reflection = this.reflection = scale > 0 && mesh.userData.water_slope !== true ? reflector({ bounces: false, samples: 0 }) : null;
    this.reflectionUV = reflection?.uvNode ?? null;
    // Reflections do not sample the main camera's screen-space GTAO context.
    const context = this.context = renderer.contextNode;
    if (reflection) {
      reflection.reflector.resolutionScale = scale;
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
      const cadence = new ReflectionCadence(QUALITY[tier].reflectionSlowFps);
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
    this.setSurface(activeSurface);
    liveRivers.add(this);
    // Opaque colour, but exclude the river from the opaque normal/velocity prepass and its recursive reflection.
    this.material.transparent = true;
    this.material.name = reflection ? 'river-planar' : 'river-sky';
  }

  /** Rebuild the material graph in place; keep streamed geometry and the reflection target resident. */
  setSurface(factory: typeof riverSurface) {
    this.disposeSurface();
    if (this.reflection) this.reflection.uvNode = this.reflectionUV;
    this.disposeSurface = factory(this.material, this.env, this.deterministic, this.reflection, this.bodyColour);
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
