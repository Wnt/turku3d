// Distant facades use the surveyed shell and its pigment, with a texture-free window rhythm.
import * as THREE from 'three/webgpu';
import { float, floor, fract, fwidth, max, mix, positionWorld, smoothstep, vec3, vertexColor } from 'three/tsl';
import { wallFrame, windowEmission } from './nightLights';

export function sceneryMaterial(buildings: boolean) {
  const material = new THREE.MeshStandardNodeMaterial({ roughness: 1, metalness: 0, vertexColors: true });
  material.name = buildings ? 'distant-city-windows' : 'distant-city-surface';
  material.flatShading = true;
  if (!buildings) return material;
  const frame = wallFrame();
  const bay = frame.along.div(2.8), storey = positionWorld.y.div(3.2);
  const x = fract(bay), y = fract(storey);
  const dx = max(fwidth(bay), .025), dy = max(fwidth(storey), .025);
  // Analytic edge filtering keeps distant panes from flickering as the camera moves.
  const pane = smoothstep(float(.22).sub(dx), float(.22).add(dx), x)
    .mul(float(1).sub(smoothstep(float(.68).sub(dx), float(.68).add(dx), x)))
    .mul(smoothstep(float(.25).sub(dy), float(.25).add(dy), y))
    .mul(float(1).sub(smoothstep(float(.77).sub(dy), float(.77).add(dy), y)))
    .mul(frame.wall);
  material.colorNode = mix(vertexColor().rgb, vec3(.075, .105, .125), pane.mul(.78));
  material.emissiveNode = windowEmission(frame.plane, floor(bay), floor(storey), y, pane);
  return material;
}

export function styleScenery(model: THREE.Object3D) {
  const originals = new Set<THREE.Material>();
  const materials = new Map<boolean, THREE.MeshStandardNodeMaterial>();
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) originals.add(material);
    const buildings = mesh.userData.layer === 'buildings' || mesh.name === 'buildings';
    let material = materials.get(buildings);
    if (!material) { material = sceneryMaterial(buildings); materials.set(buildings, material); }
    mesh.material = material;
    mesh.castShadow = false; mesh.receiveShadow = false;
  });
  for (const material of originals) material.dispose();
}

// Material experiments are applied explicitly to resident scenery through the dev session.
if (import.meta.hot) import.meta.hot.accept();
