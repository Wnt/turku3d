import * as THREE from 'three/webgpu';
import { cameraPosition, dot, float, max, mix, normalize, positionWorld, pow, reflect, reflector,
  texture, time, uniform, vec2, vec3 } from 'three/tsl';
import { QUALITY, type QualityTier } from './quality';
import type { Environment } from './environment';

/** Periodic normal map from a small directional wave spectrum: no external asset request or seams. */
export function waveNormalTexture() {
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
  private reflection = reflector({ bounces: false, samples: 0 });

  constructor(mesh: THREE.Mesh, env: Environment, tier: QualityTier, deterministic: boolean) {
    this.reflection.reflector.resolutionScale = QUALITY[tier].reflectionScale;
    const target = this.reflection.target;
    const p = mesh.geometry.getAttribute('position'), n = mesh.geometry.getAttribute('normal');
    target.position.fromBufferAttribute(p, 0);
    target.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1),
      n ? new THREE.Vector3().fromBufferAttribute(n, 0).normalize() : new THREE.Vector3(0, 1, 0));
    mesh.add(target);
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

  dispose() {
    this.reflection.target.removeFromParent();
    for (const target of this.reflection.reflector.renderTargets.values()) target.dispose();
    this.reflection.reflector.renderTargets.clear();
    this.reflection.dispose();
    this.normals.dispose();
    this.material.dispose();
  }
}
