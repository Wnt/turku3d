// The street-object material families: what the signpost, lamp and signal kinds name beyond plain metal and paint.
//
// `sign`: a plate's face is a picture in the object store (`params.atlas`, 1024 px per metre, drawn by the pipeline
// from the sign catalogue); the plate's front triangles address it with TEXCOORD_0, 0..1 across the plate with v
// down. `lens`: the glass of a street light or a signal head, lit by its own colour (the glTF emissive factor).
import * as THREE from 'three/webgpu';
import { registerFamily, storeTexture } from '../materials';

registerFamily('sign', (params, fallback) => {
  const material = new THREE.MeshStandardNodeMaterial({ roughness: fallback.roughness, metalness: fallback.metalness });
  if (typeof params.atlas === 'string') material.map = storeTexture(params.atlas);
  else material.color.copy(fallback.color);
  return material;
});

registerFamily('lens', (_params, fallback) => {
  const material = new THREE.MeshStandardNodeMaterial({ roughness: fallback.roughness, metalness: fallback.metalness });
  material.color.copy(fallback.color);
  material.emissive.copy(fallback.emissive);
  material.emissiveIntensity = fallback.emissiveIntensity;
  return material;
});
