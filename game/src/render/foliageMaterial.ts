import * as THREE from 'three/webgpu';
import { float, specularColor, specularColorBlended, specularF90, vec3 } from 'three/tsl';

/** Share of the dielectric specular (F0 0.04, F90 1) that leaf and impostor cards keep. A card reflects the open sky,
 *  but inside a crown most of that sky is other leaves: at full strength the sky's blue made shaded crowns B/G 0.72
 *  even without the bloom veil (panorama 0.21); half of it, with the lighter crown light, gives 0.2. */
export const FOLIAGE_SPECULAR = 0.5;
/** Leaf, impostor and far canopy surfaces: a standard material with FOLIAGE_SPECULAR. Clones (LOD variants) keep the
 *  class. */
export class FoliageMaterial extends THREE.MeshStandardNodeMaterial {
  // Its own type keeps its program apart from a standard material with the same nodes (customProgramCacheKey).
  static get type() { return 'FoliageMaterial'; }

  setupSpecular() {
    const f0 = vec3(0.04 * FOLIAGE_SPECULAR);
    specularColor.assign(f0);
    specularColorBlended.assign(f0);
    specularF90.assign(float(FOLIAGE_SPECULAR));
  }
}
