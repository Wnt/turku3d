import * as THREE from 'three/webgpu';
import { attribute, materialColor } from 'three/tsl';
import { nightUniforms } from './nightLights';
import { wetSurface } from './weather/surfaces';
import rules from '../../../pipeline/turku3d/night_lighting.json';

/**
 * Lower-quay stone under the LED bars: the ordinary stone maps plus a generator-baked
 * downward wash (_NIGHT_IRRADIANCE, tint included) on the shared night schedule.
 * The bars themselves are `night_quay_bar` emitters handled by nightSurface().
 * No light slots, render targets or per-frame work.
 */
export function quayWashMaterial(source: THREE.Material, renderer: THREE.WebGPURenderer) {
  const material = renderer.library.fromMaterial(source.clone()) as THREE.MeshStandardNodeMaterial;
  material.emissiveNode = attribute('_night_irradiance', 'vec3').mul(materialColor.rgb)
    .mul(rules.quay.washIntensity).mul(nightUniforms.night);
  material.emissiveIntensity = 1;
  return wetSurface(material);
}
