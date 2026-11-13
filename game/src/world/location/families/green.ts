// The planting material families of the world pipeline (pipeline/turku3d/world/green/treegen.py):
//
// - `foliage`: alpha-tested leaf cards on the nature library's foliage atlas. TEXCOORD_0 is the atlas UV of a card,
//   COLOR_0 the crown-depth ambient occlusion, `_wind` the sway weights, the normals point out of the crown.
// - `bark`: closed branch tubes on the library's bark column atlas (u round the branch, v in metres along it).
// - `hedge`: the opaque leafy body of a hedge or a clipped shrub; COLOR_0 is its shading, the leaf colour and its
//   variation is drawn in world space on the material's base colour.
//
// The textures and the leaf shading are the nature library's (world/nature/materials.ts), so a generated tree draws
// with the calibrated leaves, bark, wind and sun-through-leaf of the library's own trees.
import * as THREE from 'three/webgpu';
import { float, mix, mx_noise_float, positionWorld, vec3, vertexColor } from 'three/tsl';
import { cloneNodeMaterial } from '../../../render/lodFade';
import type { NatureMaterials } from '../../nature/materials';
import { registerFamily } from '../materials';

export function registerGreenFamilies(nature: NatureMaterials) {
  // One material each for every tree and shrub: the atlas cell and the bark slot are in the UVs, so all of them
  // share one pipeline instead of compiling one per tree model.
  const leaves = cloneNodeMaterial(nature.standingLeaves), bark = cloneNodeMaterial(nature.bark);
  registerFamily('foliage', () => leaves);
  registerFamily('bark', () => bark);
  registerFamily('hedge', (_params, fallback) => {
    const material = new THREE.MeshStandardNodeMaterial({ roughness: 0.95, metalness: 0 });
    const leaf = fallback.color;
    // Clumps of leaves at two scales, in world space so that a hedge has no repeat and no seam between cells.
    const clump = mx_noise_float(positionWorld.mul(9)).mul(0.5).add(0.5);
    const patch = mx_noise_float(positionWorld.mul(1.7).add(vec3(11.3, 4.1, 7.7))).mul(0.5).add(0.5);
    const tone = mix(float(0.55), float(1.25), clump).mul(mix(float(0.85), float(1.12), patch));
    material.colorNode = vec3(leaf.r, leaf.g, leaf.b).mul(tone).mul(vertexColor().r);
    return material;
  });
}
