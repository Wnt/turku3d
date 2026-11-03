import * as THREE from 'three/webgpu';
import { cameraFar, cameraNear, cameraPosition, cameraViewMatrix, dot, exp, float, length, luminance, max, mix, normalize,
  perspectiveDepthToViewZ, positionView, positionWorld, pow, reflect, reflector, screenUV, smoothstep, texture, uniform, vec2,
  vec3, vec4, velocity, pmremTexture } from 'three/tsl';
import type { Environment } from './environment';
import { nightUniforms } from './nightLights';
import { riverClock, riverWaveOffsets, riverWavePixels } from './riverWaves';
import { WATER_SWITCHES } from './waterSwitches';
import type { WaterHistory } from './waterHistory';
import { riverScreenReflection } from './riverReflection';

/** W5 (GTA V water study, loops/techstudy REPORT.md §8): GTA's river shader uses a very tight Phong sun lobe (falloff
 *  1,600-2,000, intensity 4-10) and wrap diffuse. The lobe is energy-normalised ((n + 2) / 2pi) so the sun path keeps
 *  about twice the earlier lobe's energy while it breaks into small bright glints. Far water's mip-averaged slopes
 *  under-represent its sub-pixel ripples, so the exponent relaxes with distance (Toksvig-like) and the far sun path
 *  stays a continuous streak instead of aliasing. */
export const RIVER_SUN = { near: 1800, far: 220, nearDistance: 25, farDistance: 450, energy: 0.025, broad: 0.018,
  /** Wrap-diffuse wrap and the share of the body colour that follows the sun-facing wave slopes by day. */
  wrap: 0.6, relief: 0.6 };

/** W3: GTA bakes a depth/colour texture per water body; our river reads the previous frame's opaque depth instead (the
 *  river itself writes none), so the measured body colour thickens over this many metres of water below the surface
 *  (1 - exp(-d / visibility)): quay walls and banks show through a soft, murky band at the waterline. The Aura is
 *  turbid clay water; the band is a few tens of centimetres deep. */
export const RIVER_DEPTH = { visibility: 0.45 };

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
  reflection: ReturnType<typeof reflector> | null, bodyColour = new THREE.Color(0.075, 0.062, 0.035),
  history: WaterHistory | null = null, screenReflection = false): () => void {
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
  const swell = texture(waves, uv.mul(0.19).add(flow.xy)).rg.sub(0.5).mul(0.32);
  const ripple = texture(waves, uv.yx.mul(0.6).add(flow.zw)).rg.sub(0.5).mul(0.24);
  const slope = swell.add(ripple.yx);
  const normal = normalize(vec3(slope.x, 1, slope.y));
  const view = normalize(cameraPosition.sub(positionWorld));
  const grazing = float(1).sub(max(dot(view, normal), 0));
  const fresnel = float(0.02).add(pow(grazing, 5).mul(0.98));
  if (reflection) reflection.uvNode = reflection.uvNode!.add(cameraViewMatrix.mul(vec4(normal.x, 0, normal.z, 0)).xy.mul(vec2(-1, 1)).mul(mix(0.012, 0.021, grazing)));
  const daylight = float(1).sub(nightUniforms.night.mul(0.9));
  const sky = env.environmentMap
    ? pmremTexture(env.environmentMap, reflect(view.negate(), normal), float(0.22)) : vec3(0.18, 0.25, 0.3).mul(daylight);
  // W1 (reduced): what last frame showed along the reflected ray, over the sky where the march finds nothing. Half the
  // wave slope steers the ray: the full slope scatters grazing rays across whole facades.
  const screen = screenReflection && history?.depth && history.color && history.camera
    ? riverScreenReflection({ ...history, depth: history.depth, color: history.color, camera: history.camera }, positionWorld,
      reflect(view.negate(), normalize(vec3(slope.x.mul(0.5), 1, slope.y.mul(0.5)))), fresnel)
    : null;
  const reflected = reflection?.rgb ?? (screen ? mix(sky, screen.rgb, screen.a) : sky);
  const sunDir = uniform(env.sunDir), sunIntensity = uniform('float').onRenderUpdate(() => env.sun.intensity);
  const alignment = max(dot(reflect(sunDir.negate(), normal), view), 0);
  // Each body carries its measured water colour beneath the dynamic sky reflection.
  let sediment = uniform(bodyColour).mul(daylight);
  let specular;
  if (WATER_SWITCHES.tightSun) {
    const s = RIVER_SUN;
    const exponent = mix(float(s.near), float(s.far), smoothstep(s.nearDistance, s.farDistance, length(cameraPosition.sub(positionWorld))));
    const lobe = pow(alignment, exponent).mul(exponent.add(2).mul(s.energy / (2 * Math.PI)));
    // The planar image shows whether the sun is visible along the reflected ray: where a bank building occludes it,
    // the mirror point lies in that building's shadow, so the glint fades with the image's brightness relative to the
    // sky's own (the PMREM sky at the visible sky's display scale).
    // A screen-space hit is itself something standing between the water and the sky.
    let open: THREE.Node<'float'> = screen ? float(1).sub(screen.a) : float(1);
    if (reflection && env.environmentMap) {
      const skyScale = mix(float(1), env.atmosphere.skyBrightness, smoothstep(0.07, 0.26, sunDir.y));
      const sky = luminance(pmremTexture(env.environmentMap, reflect(view.negate(), normal), float(0.3))).mul(skyScale);
      open = smoothstep(0.3, 0.75, luminance(reflection.rgb).div(max(sky, 0.001)));
    }
    specular = lobe.mul(open).add(pow(alignment, 36).mul(s.broad)).mul(uniform(env.sun.color)).mul(sunIntensity);
    // Wrap diffuse: wave facets turned to the sun carry a lighter body colour, normalised to a flat surface's so the
    // measured colour stays the body's mean; by night (no sun) the body is flat as before.
    const wrapped = (n: THREE.Node<'vec3'>) => max(dot(n, sunDir).add(s.wrap).div(1 + s.wrap), 0);
    const relief = wrapped(normal).div(max(wrapped(vec3(0, 1, 0)), 0.05)).clamp(0.5, 1.8);
    sediment = sediment.mul(mix(float(1), relief, sunIntensity.div(3).clamp(0, 1).mul(s.relief)));
  } else {
    // Fine wave normals break the sun path into glints; a faint broad lobe retains the smaller unresolved ripples.
    specular = pow(alignment, 240).mul(0.6).add(pow(alignment, 36).mul(0.018)).mul(uniform(env.sun.color)).mul(sunIntensity);
  }
  if (history?.depth) {
    // The opaque depth behind this pixel one frame ago, where the water surface was then (its own motion vector). On
    // the same view ray, a point at view depth z_bed lies h * (z_bed / z_surface - 1) below the surface, h being the
    // camera's height above it. Something in front of the surface there (a boat, the player) means no bed reading.
    const previous = screenUV.sub((velocity as unknown as THREE.Node<'vec2'>).mul(vec2(0.5, -0.5)));
    const bed = perspectiveDepthToViewZ(texture(history.depth, previous).r, cameraNear, cameraFar).negate();
    const ratio = bed.div(positionView.z.negate().max(0.01));
    const height = cameraPosition.y.sub(positionWorld.y).max(0.05);
    const seen = previous.greaterThanEqual(0).all().and(previous.lessThanEqual(1).all()).and(ratio.greaterThan(0.995));
    const opacity = seen.select(float(1).sub(exp(height.mul(ratio.sub(1)).max(0).div(-RIVER_DEPTH.visibility))), float(1));
    // Reflection and highlight stay whole; only the body colour thins over the bank, blended onto it:
    // out = F * reflected + specular + (1 - F) * (opacity * body + (1 - opacity) * behind).
    const alpha = float(1).sub(float(1).sub(fresnel).mul(float(1).sub(opacity)));
    const lit = reflected.mul(fresnel).add(specular).add(sediment.mul(float(1).sub(fresnel)).mul(opacity));
    material.colorNode = lit.div(max(alpha, 0.001));
    material.opacityNode = alpha;
  } else {
    material.colorNode = mix(sediment, reflected, fresnel).add(specular);
    material.opacityNode = null;
  }
  // The loop's per-pose A/B driver swaps only this graph; streamed geometry stays resident.
  if (import.meta.hot) material.userData.setRiverSurface = (factory: typeof riverSurface) => {
    disposeSurface();
    if (reflection) reflection.uvNode = baseUV!;
    disposeSurface = factory(material, env, deterministic, reflection, bodyColour, history, screenReflection);
    material.needsUpdate = true;
  };
  return () => { delete material.userData.setRiverSurface; disposeSurface(); };
}
