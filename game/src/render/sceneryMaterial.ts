// Far openings retain the detailed facade's program or baked glazing mask.
import * as THREE from 'three/webgpu';
import { attribute, float, floor, fract, luminance, mix, positionWorld, smoothstep, texture, uv, varying, vec3, vertexColor } from 'three/tsl';
import { nightUniforms, wallFrame, windowEmission } from './nightLights';
import { facadeProgramSurface } from './facadeProgramSurface';
import { interleaveProgram } from './facadeProgramAttributes';
import type { SharedMaterials } from './sharedMaterials';
import farGround from './farGroundPalette.json';

const source = new THREE.MeshStandardMaterial({ name: 'distant-city-pigment', roughness: 1, metalness: 0 });
// Node identity participates in the program key. Ground and plain shells have the
// same pigment expression; share it without sharing their material lifetimes.
const sceneryPigment = vertexColor().rgb;

export function sceneryMaterial(layer: string, coverage?: THREE.DataTexture, map?: THREE.Texture | null, programVersion = 5) {
  // colorNode owns the authored pigment; vertexColors=true would multiply it a second time.
  const material = new THREE.MeshStandardNodeMaterial({ roughness: 1, metalness: 0 });
  material.colorNode = sceneryPigment;
  material.name = `distant-city-${layer}`;
  material.flatShading = true;
  if (layer === 'regional' && coverage) material.maskNode = texture(coverage, positionWorld.xz.div(250 * 256).add(.5)).r.lessThan(.5);
  // The old cached tree prototypes carry autumn pigments. Match the city's summer canopy,
  // retaining their tonal variation without turning the far skyline into bright orange spikes.
  if (layer === 'vegetation') material.colorNode = vec3(.45, .78, .30).mul(luminance(vertexColor().rgb));
  if (layer === 'night-architecture') {
    const light = attribute('_night_irradiance', 'vec4');
    material.emissiveNode = light.rgb.mul(nightUniforms.night).mul(mix(float(1), nightUniforms.useLevels.y.clamp(0, 1), light.w));
    return material;
  }
  if (layer === 'buildings-program' || layer === 'buildings-program-v6') {
    const { program, pigment, frame, emission } = facadeProgramSurface(layer === 'buildings-program-v6' ? 6 : programVersion);
    material.colorNode = mix(mix(pigment.pigment, pigment.trim, pigment.band),
      mix(frame.rgb, vec3(.016, .028, .04), program.glass), program.inside.mul(float(1).sub(pigment.band)));
    material.emissiveNode = emission;
    return material;
  }
  if (layer === 'buildings-lod-glazed') {
    // Block LOD walls whose HD facade program has windows (pipeline/turku3d/coarse/blocks.py): a soft window in each
    // 2.6 m x 3.2 m cell of the distant city's grid darkens the pigment by day and lights the same windows at night.
    const frame = wallFrame(), storey = positionWorld.y.div(3.2), bay = frame.along.div(2.6);
    const across = fract(bay), rise = fract(storey);
    const glass = smoothstep(.22, .32, across).mul(smoothstep(.78, .68, across))
      .mul(smoothstep(.25, .35, rise)).mul(smoothstep(.85, .75, rise)).mul(frame.wall);
    material.colorNode = mix(sceneryPigment, vec3(.03, .045, .06), glass.mul(.8));
    material.emissiveNode = windowEmission(frame.plane, floor(bay), floor(storey), rise, glass);
    return material;
  }
  // A surveyed shell or blank wall has no evidence of windows. Never invent them.
  if (!['buildings-mapped', 'buildings-night-mapped'].includes(layer) || !map) return material;
  material.map = map;
  const rect = varying(attribute('_far_rect', 'vec4'), 'v_far_rect').setInterpolation('flat');
  const repeat = varying(attribute('_far_repeat', 'vec2'), 'v_far_repeat').setInterpolation('flat');
  const coord = repeat.greaterThan(.5).select(fract(uv()), uv().clamp(0, 1));
  const sample = texture(map, rect.xy.add(coord.mul(rect.zw)));
  material.colorNode = sample.rgb.mul(vertexColor().rgb);
  if (layer === 'buildings-night-mapped') {
    material.emissiveNode = attribute('_night_irradiance', 'vec3').mul(sample.rgb).mul(float(1).sub(sample.a)).mul(nightUniforms.night);
    return material;
  }
  const frame = wallFrame();
  const storey = positionWorld.y.div(3.2);
  material.emissiveNode = windowEmission(frame.plane, floor(frame.along.div(2.6)), floor(storey), fract(storey), sample.a.mul(frame.wall));
  return material;
}

/** Derivatives built before farGroundPalette.json carry the old fixed ground palette (and white for unmatched keys):
 *  translate those exact quantized pigments to the near materials' distant colours, once per loaded cell. */
export function translateLegacyGround(geometry: THREE.BufferGeometry) {
  const colour = geometry.getAttribute('color') as THREE.BufferAttribute | undefined;
  if (!colour || colour.itemSize < 3 || geometry.userData.farGround) return;
  geometry.userData.farGround = true;
  const tolerance = 1.5 / 255;
  for (let i = 0; i < colour.count; i++) {
    const r = colour.getX(i), g = colour.getY(i), b = colour.getZ(i);
    for (const [from, to] of farGround.legacy) {
      if (Math.abs(r - from[0]) > tolerance || Math.abs(g - from[1]) > tolerance || Math.abs(b - from[2]) > tolerance) continue;
      colour.setXYZ(i, to[0], to[1], to[2]);
      break;
    }
  }
  colour.needsUpdate = true;
}

export function styleScenery(model: THREE.Object3D, shared?: SharedMaterials, coverage?: THREE.DataTexture) {
  const originals = new Set<THREE.Material>();
  const materials = new Map<string, THREE.MeshStandardNodeMaterial>();
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) originals.add(material);
    const layer = String(mesh.userData.layer ?? mesh.name);
    if (layer === 'ground') translateLegacyGround(mesh.geometry);
    interleaveProgram(mesh.geometry);
    const map = (mesh.material as THREE.MeshStandardMaterial).map;
    const programVersion = mesh.geometry.getAttribute('_kit_layout')?.itemSize === 4 ? 6 : 5;
    const key = `${layer}:${map?.uuid ?? ''}:${programVersion}`;
    let material = materials.get(key);
    if (!material) {
      material = (shared ? shared.styled.acquire(source, `distant-city-${key}`, model, () => sceneryMaterial(layer, coverage, map, programVersion))
        : sceneryMaterial(layer, coverage, map, programVersion)) as THREE.MeshStandardNodeMaterial;
      materials.set(key, material);
    }
    mesh.material = material;
    mesh.castShadow = false; mesh.receiveShadow = false;
  });
  for (const material of originals) if (!shared?.styled.has(material)) material.dispose();
}

const lodSource = new THREE.MeshStandardMaterial({ name: 'block-lod', roughness: 1, metalness: 0 });

/** Style a block LOD parent clone; pooled materials are released with `owner` (its tile model). */
export function styleBlockLod(root: THREE.Object3D, owner: THREE.Object3D, shared?: SharedMaterials) {
  root.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    const layer = mesh.userData.lod === 'walls-glazed' ? 'buildings-lod-glazed' : 'buildings-lod';
    mesh.material = shared ? shared.styled.acquire(lodSource, `block-lod:${layer}`, owner, () => sceneryMaterial(layer))
      : sceneryMaterial(layer);
    mesh.castShadow = true; mesh.receiveShadow = true;
  });
}

// Material experiments are applied explicitly to resident scenery through the dev session.
if (import.meta.hot) import.meta.hot.accept();
