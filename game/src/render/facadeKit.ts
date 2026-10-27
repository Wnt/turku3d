// A single shared atlas supplies upper-storey bays and a separate row at the building base.
import * as THREE from 'three/webgpu';
import { color, float, fract, mix, normalMap, step, texture, uv, vec2 } from 'three/tsl';

export function facadeKitMaterial(source: THREE.Material, renderer: THREE.WebGPURenderer): THREE.MeshStandardNodeMaterial | null {
  const p = source.userData.facade_kit as { module: number; wall: string; trim: string } | undefined;
  const original = source as THREE.MeshStandardMaterial;
  if (!p || !original.map || !original.normalMap || !original.roughnessMap) return null;
  const material = renderer.library.fromMaterial(source.clone()) as THREE.MeshStandardNodeMaterial;
  const cell = vec2(p.module % 7, Math.floor(p.module / 7) * 2).add(vec2(0, float(1).sub(step(1, uv().y))));
  // Four-pixel wrapped gutters isolate modules from neighbouring atlas cells and mip levels.
  const local = vec2(fract(uv().x), float(1).sub(fract(uv().y))).mul(120).add(4);
  const coord = cell.mul(128).add(local).div(1024);
  const albedo = texture(original.map, coord).rgb;
  const orm = texture(original.roughnessMap, coord);
  const wall = color(p.wall), trim = color(p.trim);
  const tint = mix(trim, wall, step(0.72, albedo.r));
  material.colorNode = albedo.mul(tint);
  material.normalNode = normalMap(texture(original.normalMap, coord).xyz, vec2(0.65));
  material.roughnessNode = orm.g;
  material.metalnessNode = orm.b;
  material.aoNode = orm.r;
  return material;
}
