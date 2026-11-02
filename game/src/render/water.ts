import { compileModel, nextRenderDepth, isPrecompiling } from './compileModel';
import * as THREE from 'three/webgpu';
import { reflector } from 'three/tsl';
import { REFLECTION_LAYER, ReflectionShadows, ReflectionCadence } from './reflections';
import { QUALITY, type QualityTier } from './quality';
import type { Environment } from './environment';
import { riverSurface } from './riverSurface';

const liveRivers = new Set<RiverMaterial>();

export class RiverMaterial {
  readonly material = new THREE.NodeMaterial();
  private disposeSurface = () => {};
  private reflectionUV: ReturnType<typeof reflector>['uvNode'];
  private context: THREE.WebGPURenderer['contextNode'];
  private prepared = false;
  private reflectionDepth = 3;
  private reflection: ReturnType<typeof reflector> | null;

  constructor(mesh: THREE.Mesh, private env: Environment, tier: QualityTier, private deterministic: boolean, renderer: THREE.WebGPURenderer) {
    const scale = QUALITY[tier].reflectionScale;
    const reflection = this.reflection = scale > 0 ? reflector({ bounces: false, samples: 0 }) : null;
    this.reflectionUV = reflection?.uvNode ?? null;
    // Reflections do not sample the main camera's screen-space GTAO context.
    const context = this.context = renderer.contextNode;
    if (reflection) {
      reflection.reflector.resolutionScale = scale;
      const reflector = reflection.reflector;
      const virtualCamera = reflector.getVirtualCamera.bind(reflector);
      reflector.getVirtualCamera = camera => {
        const reflected = virtualCamera(camera);
        reflected.layers.set(REFLECTION_LAYER);
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
    this.setSurface(riverSurface);
    liveRivers.add(this);
    // Opaque colour, but exclude the river from the opaque normal/velocity prepass and its recursive reflection.
    this.material.transparent = true;
    this.material.name = reflection ? 'river-planar' : 'river-sky';
  }

  /** Rebuild the material graph in place; keep streamed geometry and the reflection target resident. */
  setSurface(factory: typeof riverSurface) {
    this.disposeSurface();
    if (this.reflection) this.reflection.uvNode = this.reflectionUV;
    this.disposeSurface = factory(this.material, this.env, this.deterministic, this.reflection);
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
    const p = mesh.geometry.getAttribute('position'), n = mesh.geometry.getAttribute('normal');
    target.position.fromBufferAttribute(p, 0);
    target.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1),
      n ? new THREE.Vector3().fromBufferAttribute(n, 0).normalize() : new THREE.Vector3(0, 1, 0));
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
  if (next) for (const river of liveRivers) river.setSurface(next.riverSurface);
});
