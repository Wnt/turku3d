import * as THREE from 'three/webgpu';
import { cameraPosition, dot, float, max, mix, normalize, positionWorld, pow, reflect, reflector,
  texture, time, uniform, vec2, vec3, pmremTexture } from 'three/tsl';
import type { Environment } from './environment';
import { nightUniforms } from './nightLights';

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

/** Build just the surface graph so local water iterations can hot-replace it without reloading tiles. */
export function riverSurface(material: THREE.NodeMaterial, env: Environment, deterministic: boolean,
  reflection: ReturnType<typeof reflector> | null) {
  const normals = waveNormalTexture();
    const clock = deterministic ? float(0) : time;
    const uv = positionWorld.xz.mul(0.12);
    const n1 = texture(normals, uv.add(vec2(clock.mul(0.018), clock.mul(0.011)))).xyz.mul(2).sub(1);
    const n2 = texture(normals, uv.mul(1.73).sub(vec2(clock.mul(0.009), clock.mul(0.015)))).xyz.mul(2).sub(1);
    const normal = normalize(n1.add(n2).xzy);
    const view = normalize(cameraPosition.sub(positionWorld));
    const fresnel = float(0.02).add(pow(float(1).sub(max(dot(view, normal), 0)), 5).mul(0.98));
    if (reflection) reflection.uvNode = reflection.uvNode!.add(normal.xz.mul(0.006));
    // The unlit deep-water colour (and the no-IBL sky stand-in) darkens with the night; reflections darken by themselves.
    const daylight = float(1).sub(nightUniforms.night.mul(0.75));
    const reflected = reflection?.rgb ?? (env.environmentMap
      ? pmremTexture(env.environmentMap, reflect(view.negate(), normal), float(0.22)) : vec3(0.18, 0.25, 0.3).mul(daylight));
    const specular = pow(max(dot(reflect(uniform(env.sunDir).negate(), normal), view), 0), 180)
      .mul(uniform(env.sun.color)).mul(uniform('float').onRenderUpdate(() => env.sun.intensity));
    material.colorNode = mix(vec3(0.025, 0.065, 0.06).mul(daylight), reflected, fresnel).add(specular);
  return () => normals.dispose();
}
