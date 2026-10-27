// A single shared atlas supplies upper-storey bays and a separate row at the building base.
import * as THREE from 'three/webgpu';
import { attribute, color, float, fract, materialColor, saturation, mix, normalMap, step, texture, uv, vec2, vec3 } from 'three/tsl';

export function facadeKitMaterial(source: THREE.Material, renderer: THREE.WebGPURenderer): THREE.MeshStandardNodeMaterial | null {
  const p = source.userData.facade_kit as { version?: number; module?: number; wall?: string; trim?: string } | undefined;
  const original = source as THREE.MeshStandardMaterial;
  if (!p || !original.map || !original.normalMap || !original.roughnessMap) return null;
  if ((source as THREE.NodeMaterial).isNodeMaterial) return source as THREE.MeshStandardNodeMaterial;
  const material = renderer.library.fromMaterial(source.clone()) as THREE.MeshStandardNodeMaterial;
  // GLTFLoader exposes custom semantics in lowercase on both renderer backends.
  // Retain v1 support for published tiles until their next rebuild.
  const layout = p.version === 2 ? attribute('_kit_layout', 'vec3') : vec3(p.module!, 1, 1);
  const origin = p.version === 2 ? attribute('_kit_origin', 'vec2') : vec2(0);
  const bays = uv().sub(origin).div(layout.yz);
  const cell = vec2(layout.x.mod(7), layout.x.div(7).floor().mul(2)).add(vec2(0, float(1).sub(step(1, bays.y))));
  // Four-pixel wrapped gutters isolate modules from neighbouring atlas cells and mip levels.
  const local = vec2(fract(bays.x), float(1).sub(fract(bays.y))).mul(120).add(4);
  const coord = cell.mul(128).add(local).div(vec2(1024, 2048));
  const albedo = texture(original.map, coord).rgb;
  const orm = texture(original.roughnessMap, coord);
  const wall = p.version === 2 ? attribute('_kit_wall', 'vec3') : color(p.wall!);
  const trim = p.version === 2 ? attribute('_kit_trim', 'vec3') : color(p.trim!);
  const tint = mix(mix(vec3(1), trim, step(0.25, albedo.r)), wall, step(0.72, albedo.r));
  material.colorNode = albedo.mul(tint);
  material.normalNode = normalMap(texture(original.normalMap, coord).xyz, vec2(0.65));
  material.roughnessNode = orm.g;
  material.metalnessNode = orm.b;
  material.aoNode = orm.r;
  return material;
}

/** Draft look: 55 % saturation with a slight cool cast; drawn just in front of coplanar published terrain. */
export function tintDraft(model: THREE.Object3D, renderer: THREE.WebGPURenderer) {
  const cache = new Map<THREE.Material, THREE.Material>();
  model.traverse(o => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.castShadow = true; mesh.receiveShadow = true;
    const style = (source: THREE.Material) => {
      if (cache.has(source)) return cache.get(source)!;
      const node = facadeKitMaterial(source, renderer) ?? ((source as THREE.NodeMaterial).isNodeMaterial ? source : renderer.library.fromMaterial(source.clone())) as THREE.MeshStandardNodeMaterial;
      node.colorNode = saturation(node.colorNode ?? materialColor.rgb, 0.55).mul(vec3(0.94, 0.97, 1.04));
      node.polygonOffset = true; node.polygonOffsetFactor = -1; node.polygonOffsetUnits = -2;
      cache.set(source, node);
      return node;
    };
    mesh.material = Array.isArray(mesh.material) ? mesh.material.map(style) : style(mesh.material);
  });
}
