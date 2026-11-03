import type * as THREE from 'three/webgpu';
import { attribute, context, float, min, mix } from 'three/tsl';

/** How far baked visibility can darken indirect light (0 = none, 1 = fully to the baked value). */
export const BAKED_AMBIENT_STRENGTH = 0.7;

type Builder = { material: THREE.Material; geometry?: THREE.BufferGeometry | null };

/** Indirect-light occlusion for the beauty pass. Static tile surfaces carry a generator bake in COLOR_1
 * (docs/specs/tiles.md: R local ambient, G sky visibility, B coverage); the darker of the two terms, weighted by
 * coverage, multiplies ambient/hemisphere/environment light, never direct sun or pigment. The attribute is part of
 * the geometry cache key, so surfaces without a bake build their unchanged program. `screen` is GTAO, if any. */
export function bakedAmbientContext(screen: THREE.Node<'float'> | null = null) {
  return context({
    getAO: (input: THREE.Node<'float'> | null, builder: Builder) => {
      if (builder.material.transparent) return input;
      let ao: THREE.Node<'float'> | null = screen;
      if (builder.geometry?.hasAttribute('color_1')) {
        const baked = attribute('color_1', 'vec4');
        const occlusion = mix(float(1), min(baked.r, baked.g), baked.b.mul(BAKED_AMBIENT_STRENGTH));
        ao = ao ? ao.mul(occlusion) : occlusion;
      }
      if (!ao) return input;
      return input !== null ? input.mul(ao) : ao;
    },
  });
}
