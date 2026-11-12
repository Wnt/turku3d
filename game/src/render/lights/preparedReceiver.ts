import type * as THREE from 'three/webgpu';
import { attribute, diffuseContribution } from 'three/tsl';
import { nightUniforms } from '../nightLights';

/** The explicit producer record distinguishes these receivers from architectural light-map attributes. */
export function preparedStreetReceiver(builder: THREE.NodeBuilder) {
  return builder.geometry?.userData.preparedStreetLighting === true;
}

export function preparedStreetDiffuse() {
  // The bake already includes the Lambert 1/pi. Keep direct light out of ambient/GTAO attenuation.
  return attribute('_night_irradiance', 'vec3').mul(diffuseContribution).mul(nightUniforms.night);
}
