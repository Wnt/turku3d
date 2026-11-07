// Architectural light lives in existing opaque draws and follows the shared clock.
import type * as THREE from 'three/webgpu';
import { attribute, materialColor, materialEmissive, materialRoughness, normalWorldGeometry, smoothstep } from 'three/tsl';
import { nightUniforms } from './nightLights';
import rules from '../../../pipeline/turku3d/night_lighting.json';

/** One emissive graph per night key, shared by every material of that key: node identity is part of r186's program
 *  key, and the graph reads each draw's own material (materialEmissive, materialColor, materialRoughness). A fresh
 *  graph per material gave every generated landmark wall its own program: 208 of 339 program builds while driving on
 *  the Radeon 560X laptop (win-prof2), each 60-120 ms of main thread there. */
const graphs = new Map<string, THREE.Node>();

export function nightSurface(material: THREE.MeshStandardNodeMaterial, key: string) {
  if (!key.startsWith('night_') || material.userData.nightSurface) return material;
  const settings = rules.materials[key as keyof typeof rules.materials];
  if (!settings) return material;
  material.userData.nightSurface = true;
  let shared = graphs.get(key);
  if (!shared) graphs.set(key, shared = nightGraph(key, settings.intensity));
  material.emissiveNode = shared;
  // Keep a single graph across time changes, including zero daylight emission.
  material.emissiveIntensity = 1;
  return material;
}

function nightGraph(key: string, intensity: number): THREE.Node {
  let light = materialEmissive.mul(intensity);
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
  return light;
}
