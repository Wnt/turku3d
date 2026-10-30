import { compileModel, nextRenderDepth, isPrecompiling } from './compileModel';
import * as THREE from 'three/webgpu';
import { cameraPosition, dot, float, max, mix, normalize, positionWorld, pow, reflect, reflector,
  texture, time, uniform, vec2, vec3 } from 'three/tsl';
import { REFLECTION_LAYER, ReflectionShadows, ReflectionCadence } from './reflections';
import { QUALITY, type QualityTier } from './quality';
import type { Environment } from './environment';

/** Periodic normal map from a small directional wave spectrum: no external asset request or seams. */
function waveNormalTexture() {
  const size = 64, data = new Uint8Array(size * size * 4);
  const bands = [[3, 2, 0.035, 0.4], [7, -5, 0.02, 2.1], [13, 4, 0.012, 1.2],
    [2, 11, 0.013, 3.4], [17, -9, 0.006, 5.2]];
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const u = x / size * Math.PI * 2, v = y / size * Math.PI * 2;
    let dx = 0, dy = 0;
    for (const [kx, ky, amplitude, phase] of bands) {
      const slope = amplitude * Math.cos(u * kx + v * ky + phase) / Math.hypot(kx, ky);
      dx += kx * slope; dy += ky * slope;
    }
    const n = new THREE.Vector3(-dx, -dy, 1).normalize();
    data.set([Math.round((n.x * 0.5 + 0.5) * 255), Math.round((n.y * 0.5 + 0.5) * 255),
      Math.round((n.z * 0.5 + 0.5) * 255), 255], (y * size + x) * 4);
  }
  const map = new THREE.DataTexture(data, size, size);
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.magFilter = THREE.LinearFilter; map.minFilter = THREE.LinearMipmapLinearFilter;
  map.generateMipmaps = true; map.needsUpdate = true;
  return map;
}

export class RiverMaterial {
  readonly material = new THREE.NodeMaterial();
  private normals = waveNormalTexture();
  private context: THREE.WebGPURenderer['contextNode'];
  private prepared = false;
  private reflectionDepth = 3;
  private reflection = reflector({ bounces: false, samples: 0 });

  constructor(mesh: THREE.Mesh, env: Environment, tier: QualityTier, deterministic: boolean, renderer: THREE.WebGPURenderer) {
    this.reflection.reflector.resolutionScale = QUALITY[tier].reflectionScale;
    // Reflections do not sample the main camera's screen-space GTAO context.
    const context = this.context = renderer.contextNode;
    const reflector = this.reflection.reflector;
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
    const clock = deterministic ? float(0) : time;
    const uv = positionWorld.xz.mul(0.12);
    const n1 = texture(this.normals, uv.add(vec2(clock.mul(0.018), clock.mul(0.011)))).xyz.mul(2).sub(1);
    const n2 = texture(this.normals, uv.mul(1.73).sub(vec2(clock.mul(0.009), clock.mul(0.015)))).xyz.mul(2).sub(1);
    const normal = normalize(n1.add(n2).xzy);
    const view = normalize(cameraPosition.sub(positionWorld));
    const fresnel = float(0.02).add(pow(float(1).sub(max(dot(view, normal), 0)), 5).mul(0.98));
    this.reflection.uvNode = this.reflection.uvNode!.add(normal.xz.mul(0.006));
    const specular = pow(max(dot(reflect(uniform(env.sunDir).negate(), normal), view), 0), 180)
      .mul(uniform(env.sun.color)).mul(uniform('float').onRenderUpdate(() => env.sun.intensity));
    this.material.colorNode = mix(vec3(0.025, 0.065, 0.06), this.reflection.rgb, fresnel).add(specular);
    // Opaque colour, but exclude the river from the opaque normal/velocity prepass and its recursive reflection.
    this.material.transparent = true;
    this.material.name = 'river-planar';
  }

  /** Compile every eligible surface in this exact target/camera context before publishing streamed content. */
  async prepare(renderer: THREE.WebGPURenderer, model: THREE.Object3D, scene: THREE.Scene, camera: THREE.PerspectiveCamera) {
    const reflected = this.reflection.reflector.getVirtualCamera(camera);
    const target = this.reflection.reflector.getRenderTarget(reflected);
    if (!this.prepared && model !== scene) await this.prepare(renderer, scene, scene, camera);
    await compileModel(renderer, model, scene, reflected as THREE.PerspectiveCamera, target, null, this.context, false, this.reflectionDepth);
    this.prepared = true;
  }

  /** Transfer the shared reflection plane when its original tile unloads. */
  attach(mesh: THREE.Mesh) {
    const target = this.reflection.target;
    const p = mesh.geometry.getAttribute('position'), n = mesh.geometry.getAttribute('normal');
    target.position.fromBufferAttribute(p, 0);
    target.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1),
      n ? new THREE.Vector3().fromBufferAttribute(n, 0).normalize() : new THREE.Vector3(0, 1, 0));
    mesh.add(target);
  }

  dispose() {
    this.reflection.target.removeFromParent();
    for (const target of this.reflection.reflector.renderTargets.values()) target.dispose();
    this.reflection.reflector.renderTargets.clear();
    this.reflection.dispose();
    this.normals.dispose();
    this.material.dispose();
  }
}
