import * as THREE from 'three/webgpu';
import { faceforward, float, positionView, specularColor, specularColorBlended, specularF90, vec3 } from 'three/tsl';

/** Share of the dielectric specular (F0 0.04, F90 1) that leaf and impostor cards keep. A card reflects the open sky,
 *  but inside a crown most of that sky is other leaves: at full strength the sky's blue made shaded crowns B/G 0.72
 *  even without the bloom veil (panorama 0.21); half of it, with the lighter crown light, gives 0.2. */
export const FOLIAGE_SPECULAR = 0.5;
/** Leaf, impostor and far canopy surfaces: a standard material with FOLIAGE_SPECULAR. Clones (LOD variants) keep the
 *  class. */
export class FoliageMaterial extends THREE.MeshStandardNodeMaterial {
  // Its own type keeps its program apart from a standard material with the same nodes (customProgramCacheKey).
  static get type() { return 'FoliageMaterial'; }

  setupNormal() {
    const normal = super.setupNormal();
    // Custom crown normals bypass three's back-face normal flip. A crown's far wall then catches sunlight as an
    // exterior even when seen from inside, making its shell read inside out through the leaf gaps. Orient the
    // shading normal towards the eye on both mesh and impostor cards. Use the normal itself, not triangle winding:
    // folded cards have bent crown normals, and a mirrored instance reverses winding independently of its normals.
    return this.side === THREE.DoubleSide ? faceforward(normal, positionView, normal) : normal;
  }

  setupSpecular() {
    const f0 = vec3(0.04 * FOLIAGE_SPECULAR);
    specularColor.assign(f0);
    specularColorBlended.assign(f0);
    specularF90.assign(float(FOLIAGE_SPECULAR));
  }
}
