// Far openings retain the detailed facade's program or baked glazing mask.
import * as THREE from 'three/webgpu';
import { attribute, float, floor, fract, luminance, mix, positionWorld, texture, uv, varying, vec3, vertexColor } from 'three/tsl';
import { wallFrame, windowEmission } from './nightLights';
import { facadeProgramSurface } from './facadeProgramSurface';
import { interleaveProgram } from './facadeProgramAttributes';
import type { SharedMaterials } from './sharedMaterials';

const source = new THREE.MeshStandardMaterial({ name: 'distant-city-pigment', roughness: 1, metalness: 0 });

export function sceneryMaterial(layer: string, coverage?: THREE.DataTexture, map?: THREE.Texture | null) {
  // colorNode owns the authored pigment; vertexColors=true would multiply it a second time.
  const material = new THREE.MeshStandardNodeMaterial({ roughness: 1, metalness: 0 });
  material.colorNode = vertexColor().rgb;
  material.name = `distant-city-${layer}`;
  material.flatShading = true;
  if (layer === 'regional' && coverage) material.maskNode = texture(coverage, positionWorld.xz.div(250 * 256).add(.5)).r.lessThan(.5);
  // The old cached tree prototypes carry autumn pigments. Match the city's summer canopy,
  // retaining their tonal variation without turning the far skyline into bright orange spikes.
  if (layer === 'vegetation') material.colorNode = vec3(.45, .78, .30).mul(luminance(vertexColor().rgb));
  if (layer === 'buildings-program') {
    const { program, pigment, frame, emission } = facadeProgramSurface();
    material.colorNode = mix(mix(pigment.pigment, pigment.trim, pigment.band),
      mix(frame.rgb, vec3(.016, .028, .04), program.glass), program.inside.mul(float(1).sub(pigment.band)));
    material.emissiveNode = emission;
    return material;
  }
  // A surveyed shell or blank wall has no evidence of windows. Never invent them.
  if (layer !== 'buildings-mapped' || !map) return material;
  material.map = map;
  const rect = varying(attribute('_far_rect', 'vec4'), 'v_far_rect').setInterpolation('flat');
  const repeat = varying(attribute('_far_repeat', 'vec2'), 'v_far_repeat').setInterpolation('flat');
  const coord = repeat.greaterThan(.5).select(fract(uv()), uv().clamp(0, 1));
  const sample = texture(map, rect.xy.add(coord.mul(rect.zw)));
  material.colorNode = sample.rgb.mul(vertexColor().rgb);
  const frame = wallFrame();
  const storey = positionWorld.y.div(3.2);
  material.emissiveNode = windowEmission(frame.plane, floor(frame.along.div(2.6)), floor(storey), fract(storey), sample.a.mul(frame.wall));
  return material;
}

export function styleScenery(model: THREE.Object3D, shared?: SharedMaterials, coverage?: THREE.DataTexture) {
  const originals = new Set<THREE.Material>();
  const materials = new Map<string, THREE.MeshStandardNodeMaterial>();
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) originals.add(material);
    const layer = String(mesh.userData.layer ?? mesh.name);
    interleaveProgram(mesh.geometry);
    const map = (mesh.material as THREE.MeshStandardMaterial).map;
    const key = `${layer}:${map?.uuid ?? ''}`;
    let material = materials.get(key);
    if (!material) {
      material = (shared ? shared.styled.acquire(source, `distant-city-${key}`, model, () => sceneryMaterial(layer, coverage, map))
        : sceneryMaterial(layer, coverage, map)) as THREE.MeshStandardNodeMaterial;
      materials.set(key, material);
    }
    mesh.material = material;
    mesh.castShadow = false; mesh.receiveShadow = false;
  });
  for (const material of originals) if (!shared?.styled.has(material)) material.dispose();
}

// Material experiments are applied explicitly to resident scenery through the dev session.
if (import.meta.hot) import.meta.hot.accept();
