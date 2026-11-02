import * as THREE from 'three/webgpu';
import { cameraPosition, cameraViewMatrix, dot, float, max, mix, normalize, positionWorld, pow, reflect, reflector,
  texture, uniform, vec2, vec3, vec4, pmremTexture } from 'three/tsl';
import type { Environment } from './environment';
import { nightUniforms } from './nightLights';
import { riverClock, riverWaveOffsets, riverWavePixels } from './riverWaves';

/** One tiny mipmapped slope field, sampled at crossing scales for long ripples and fine capillary detail. */
function waveTexture() {
  const size = 128, map = new THREE.DataTexture(riverWavePixels(size), size, size);
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.magFilter = THREE.LinearFilter; map.minFilter = THREE.LinearMipmapLinearFilter;
  map.generateMipmaps = true; map.needsUpdate = true;
  map.name = 'Aura directional wave slopes';
  return map;
}

/** Build just the surface graph so local water iterations can hot-replace it without reloading tiles. */
export function riverSurface(material: THREE.NodeMaterial, env: Environment, deterministic: boolean,
  reflection: ReturnType<typeof reflector> | null): () => void {
  const waves = waveTexture();
  const baseUV = reflection?.uvNode;
  let disposeSurface = () => waves.dispose();
  const flow = uniform(new THREE.Vector4());
  flow.onRenderUpdate(() => {
    // The main world frame supplies server-synchronised UTC; fixed QA poses use phase zero.
    flow.value.fromArray(riverWaveOffsets(deterministic ? 0 : riverClock.seconds));
    return flow.value;
  });
  const uv = positionWorld.xz;
  const swell = texture(waves, uv.mul(0.12).add(flow.xy)).rg.sub(0.5).mul(0.48);
  const ripple = texture(waves, uv.yx.mul(0.6).add(flow.zw)).rg.sub(0.5).mul(0.24);
  const slope = swell.add(ripple.yx);
  const normal = normalize(vec3(slope.x, 1, slope.y));
  const view = normalize(cameraPosition.sub(positionWorld));
  const grazing = float(1).sub(max(dot(view, normal), 0));
  const fresnel = float(0.02).add(pow(grazing, 5).mul(0.98));
  if (reflection) reflection.uvNode = reflection.uvNode!.add(cameraViewMatrix.mul(vec4(normal.x, 0, normal.z, 0)).xy.mul(vec2(-1, 1)).mul(mix(0.012, 0.028, grazing)));
  const daylight = float(1).sub(nightUniforms.night.mul(0.9));
  const reflected = reflection?.rgb ?? (env.environmentMap
    ? pmremTexture(env.environmentMap, reflect(view.negate(), normal), float(0.22)) : vec3(0.18, 0.25, 0.3).mul(daylight));
  const alignment = max(dot(reflect(uniform(env.sunDir).negate(), normal), view), 0);
  // Fine wave normals break the sun path into glints; a faint broad lobe retains the smaller unresolved ripples.
  const specular = pow(alignment, 240).mul(0.6).add(pow(alignment, 36).mul(0.018))
    .mul(uniform(env.sun.color)).mul(uniform('float').onRenderUpdate(() => env.sun.intensity));
  // The Aura carries suspended clay: opaque warm olive-brown below the cool sky reflection.
  const sediment = vec3(0.075, 0.062, 0.035).mul(daylight);
  material.colorNode = mix(sediment, reflected, fresnel).add(specular);
  // The loop's per-pose A/B driver swaps only this graph; streamed geometry stays resident.
  if (import.meta.hot) material.userData.setRiverSurface = (factory: typeof riverSurface) => {
    disposeSurface();
    if (reflection) reflection.uvNode = baseUV!;
    disposeSurface = factory(material, env, deterministic, reflection);
    material.needsUpdate = true;
  };
  return () => { delete material.userData.setRiverSurface; disposeSurface(); };
}
