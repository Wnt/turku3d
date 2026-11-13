// The planting material families of the world pipeline (pipeline/turku3d/world/green/treegen.py):
//
// - `foliage`: alpha-tested leaf cards on the nature library's foliage atlas. TEXCOORD_0 is the atlas UV of a card,
//   COLOR_0 = (crown-depth ambient occlusion, leaf brightness, leaf warmth), `_wind` the sway weights, the normals
//   point out of the crown.
// - `bark`: closed branch tubes on the library's bark column atlas (u round the branch, v in metres along it).
// - `hedge`: the opaque leafy body of a hedge or a clipped shrub; COLOR_0 is its shading, the leaf colour and its
//   variation is drawn in world space on the material's base colour.
//
// The textures and the leaf shading are the nature library's (world/nature/materials.ts), so a generated tree draws
// with the calibrated leaves, bark, wind and sun-through-leaf of the library's own trees. The leaf colour is
// corrected here against the campaign's street photos (LEAF_GRADE) and varied from tree to tree (STAND).
import * as THREE from 'three/webgpu';
import { float, luminance, mix, mx_noise_float, positionWorld, vec3, vec4, vertexColor } from 'three/tsl';
import { cloneNodeMaterial } from '../../../render/lodFade';
import type { NatureMaterials } from '../../nature/materials';
import { registerFamily } from '../materials';

// Measured on the ten judged views of Portsa and Humalistonkatu (2026-11-13): the library's leaves had the photos'
// red to green ratio and brightness but 0.2 of green in blue (linear) where the photos' crowns have 0.3 in sun and
// 0.6 in shade, which reads as a yellow-green. The leaf is less saturated and a little cooler.
const LEAF_GRADE = { saturation: 0.85, tint: [1.15, 1.15, 1.1] as const };
/** Tree-to-tree variation: the size of a patch of one tone in metres, and how far brightness and warmth go. */
const STAND = { size: 9, brightness: 0.16, warmth: 0.06 };

export function registerGreenFamilies(nature: NatureMaterials) {
  // One material each for every tree and shrub: the atlas cell and the bark slot are in the UVs, so all of them
  // share one pipeline instead of compiling one per tree model.
  const leaves = cloneNodeMaterial(nature.standingLeaves), bark = cloneNodeMaterial(nature.bark);
  const base = nature.standingLeaves.colorNode as THREE.Node<'vec4'>;
  const tone = vertexColor().g, warmth = vertexColor().b.sub(1);
  // No two trees of a street are the same green: brightness and warmth drift over the ground at about a crown's
  // width, the same for the near instances and the far meshes of a tree because both stand at the same place.
  const ground = vec3(positionWorld.x, 0, positionWorld.z);
  const light = mx_noise_float(ground.mul(1 / STAND.size)).mul(STAND.brightness).add(1);
  const warm = mx_noise_float(ground.mul(1 / STAND.size).add(vec3(31.7, 0, 12.9))).mul(STAND.warmth).add(warmth);
  const graded = mix(vec3(luminance(base.rgb)), base.rgb, LEAF_GRADE.saturation).mul(vec3(...LEAF_GRADE.tint));
  leaves.colorNode = vec4(graded.mul(tone).mul(light).mul(vec3(warm.add(1), warm.mul(0.35).add(1), warm.oneMinus())), base.a);
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
