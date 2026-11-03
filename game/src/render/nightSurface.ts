// Architectural light lives in existing opaque draws and follows the shared clock.
import type * as THREE from 'three/webgpu';
import { attribute, materialColor, materialEmissive, materialRoughness, normalWorldGeometry, smoothstep } from 'three/tsl';
import { nightUniforms } from './nightLights';
import rules from '../../../pipeline/turku3d/night_lighting.json';

export function nightSurface(material: THREE.MeshStandardNodeMaterial, key: string) {
  if (!key.startsWith('night_') || material.userData.nightSurface) return material;
  const settings = rules.materials[key as keyof typeof rules.materials];
  if (!settings) return material;
  material.userData.nightSurface = true;
  let light = materialEmissive.mul(settings.intensity);
  if (key === 'night_bridge_soffit') {
    // Only downward faces receive the under-deck wash. Deck, railing and walls stay dark.
    light = light.mul(materialColor.rgb).mul(smoothstep(.25, .8, normalWorldGeometry.y.negate()));
  } else if (key === 'night_landmark_wall') {
    // Generator-baked direction/height mask, never a per-building real light or shader variant.
    light = attribute('_night_irradiance', 'vec3').mul(materialColor.rgb)
      .mul(smoothstep(.15, .55, materialRoughness));
  }
  light = light.mul(nightUniforms.night);
  if (key === 'night_boat_cabin') {
    light = light.mul(nightUniforms.useLevels.y.clamp(0, 1));
  }
  material.emissiveNode = light;
  // Keep a single graph across time changes, including zero daylight emission.
  material.emissiveIntensity = 1;
  return material;
}
