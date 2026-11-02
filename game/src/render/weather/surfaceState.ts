import * as THREE from 'three/webgpu';
import { texture, uniform } from 'three/tsl';
export const weatherUniforms = { wetness: uniform(0), rain: uniform(0), seconds: uniform(0) };
const placeholder = new THREE.DataTexture(new Float32Array([65536, 0, 0, 0]), 1, 1, THREE.RGBAFormat, THREE.FloatType);
placeholder.magFilter = placeholder.minFilter = THREE.NearestFilter; placeholder.needsUpdate = true;
export const weatherHeightField = { map: texture(placeholder), origin: uniform(new THREE.Vector2()), ready: uniform(0) };
