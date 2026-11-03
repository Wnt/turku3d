import * as THREE from 'three/webgpu';
import { renderGroup, texture, uniform } from 'three/tsl';
/** Scene-wide values (a clock that changes every frame) live in the shared render group, as nightUniforms do: in the
 *  default per-object group every wet draw rewrote its own uniform buffer each frame (uniform census, 20261103). */
export const weatherUniforms = { wetness: uniform(0).setGroup(renderGroup), rain: uniform(0).setGroup(renderGroup),
  seconds: uniform(0).setGroup(renderGroup) };
const placeholder = new THREE.DataTexture(new Float32Array([65536, 0, 0, 0]), 1, 1, THREE.RGBAFormat, THREE.FloatType);
placeholder.magFilter = placeholder.minFilter = THREE.NearestFilter; placeholder.needsUpdate = true;
export const weatherHeightField = { map: texture(placeholder), origin: uniform(new THREE.Vector2()).setGroup(renderGroup),
  ready: uniform(0).setGroup(renderGroup) };
