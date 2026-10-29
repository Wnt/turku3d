// A single shared atlas supplies upper-storey bays and a separate row at the building base.
import * as THREE from 'three/webgpu';
import { attribute, color, float, fract, mix, normalMap, step, texture, uv, vec2, vec3 } from 'three/tsl';
import { programLayout, programPigment } from './facadeProgram';
import { interleaveProgram } from './facadeProgramAttributes';

export function facadeKitMaterial(source: THREE.Material, renderer: THREE.WebGPURenderer): THREE.MeshStandardNodeMaterial | null {
  const p = source.userData.facade_kit as { version?: number; module?: number; wall?: string; trim?: string } | undefined;
  const original = source as THREE.MeshStandardMaterial;
  if (!p || !original.map || !original.normalMap || !original.roughnessMap) return null;
  if ((source as THREE.NodeMaterial).isNodeMaterial) return source as THREE.MeshStandardNodeMaterial;
  const material = renderer.library.fromMaterial(source.clone()) as THREE.MeshStandardNodeMaterial;
  // GLTFLoader exposes custom semantics in lowercase on both renderer backends.
  // Retain v1 support for published tiles until their next rebuild.
  const layout = (p.version ?? 1) >= 2 ? attribute('_kit_layout', 'vec3') : vec3(p.module!, 1, 1);
  const origin = p.version === 4 ? attribute('_kit_extent', 'vec4').xy
    : (p.version ?? 1) >= 2 ? attribute('_kit_origin', 'vec2') : vec2(0);
  const metres = uv().sub(origin);
  const floors = (p.version ?? 1) >= 3 ? attribute('_kit_floors', 'vec3') : vec3(layout.z, layout.z, 0);
  const upper = step(floors.x, metres.y);
  const bays = vec2(metres.x.div(layout.y), mix(metres.y.div(floors.x), metres.y.sub(floors.x).div(floors.y).add(1), upper));
  // The entrance module contains one door; use the window module in the other bays.
  const entrance = layout.x.lessThan(42).and(layout.x.mod(3).equal(1));
  const otherBay = bays.x.floor().notEqual(floors.z);
  const selected = (p.version ?? 1) >= 3 ? entrance.and(otherBay).and(upper.lessThan(1)).select(layout.x.sub(1), layout.x) : layout.x;
  const program = p.version === 4 ? programLayout(bays, metres, layout, upper, selected) : null;
  const module = program?.module ?? selected;
  const cell = vec2(module.mod(7), module.div(7).floor().mul(2)).add(vec2(0, float(1).sub(upper)));
  // Four-pixel wrapped gutters isolate modules from neighbouring atlas cells and mip levels.
  const fraction = program?.local ?? vec2(fract(bays.x), fract(bays.y));
  const local = vec2(fraction.x, float(1).sub(fraction.y)).mul(120).add(4);
  const coord = cell.mul(128).add(local).div(vec2(1024, 2048));
  // Derive mip footprints before fract: a bay edge is not a jump across the atlas.
  const continuous = bays.mul(vec2(120 / 1024, -120 / 2048));
  const dx = continuous.dFdx(), dy = continuous.dFdy();
  const sample = texture(original.map, coord).grad(dx, dy);
  const albedo = sample.rgb;
  const orm = texture(original.roughnessMap, coord).grad(dx, dy);
  const wallColour = (p.version ?? 1) >= 2 ? attribute('_kit_wall', 'vec3') : color(p.wall!);
  const trim = (p.version ?? 1) >= 2 ? attribute('_kit_trim', 'vec3') : color(p.trim!);
  const paint = program ? programPigment(metres, bays, upper, layout, wallColour.rgb, trim.rgb, program) : null;
  const wall = paint?.pigment ?? wallColour;
  const tint = (p.version ?? 1) >= 3
    ? vec3(1).add(wall.sub(1).mul(float(1).sub(sample.a.mul(2)).clamp(0, 1))).add(trim.sub(1).mul(sample.a.mul(2).sub(1).clamp(0, 1)))
    : mix(mix(vec3(1), trim, step(0.25, albedo.r)), wall, step(0.72, albedo.r));
  material.colorNode = paint ? mix(albedo.mul(tint).mul(float(1).sub(paint.balcony)), paint.trim, paint.band) : albedo.mul(tint);
  const normal = texture(original.normalMap, coord).grad(dx, dy).xyz;
  material.normalNode = normalMap(paint ? mix(normal, vec3(.5, .5, 1), paint.band) : normal, vec2(0.65));
  material.roughnessNode = paint ? mix(orm.g, float(.85), paint.band) : orm.g;
  material.metalnessNode = paint ? mix(orm.b, float(0), paint.band) : orm.b;
  material.aoNode = orm.r;
  return material;
}

/** Bind draft facade materials and draw just in front of coplanar published terrain. */
export function tintDraft(model: THREE.Object3D, renderer: THREE.WebGPURenderer) {
  const cache = new Map<THREE.Material, THREE.Material>();
  model.traverse(o => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    interleaveProgram(mesh.geometry);
    mesh.castShadow = true; mesh.receiveShadow = true;
    let vegetation = false;
    for (let owner: THREE.Object3D | null = mesh; owner; owner = owner.parent) {
      if (owner.userData.layer === 'vegetation') { vegetation = true; break; }
    }
    const style = (source: THREE.Material) => {
      if (cache.has(source)) return cache.get(source)!;
      const pbr = source as THREE.MeshStandardMaterial;
      let node: THREE.MeshStandardMaterial | THREE.MeshStandardNodeMaterial;
      if (vegetation && pbr.type === 'MeshStandardMaterial' && !pbr.vertexColors &&
          !Object.values(pbr).some(value => value?.isTexture)) {
        // Keep ordinary PBR
        // materials so runtime LOD can combine bark/crown with vertex colours.
        node = pbr.clone();
      } else {
        node = facadeKitMaterial(source, renderer) ?? ((source as THREE.NodeMaterial).isNodeMaterial ? source : renderer.library.fromMaterial(source.clone())) as THREE.MeshStandardNodeMaterial;
      }
      node.polygonOffset = true; node.polygonOffsetFactor = -1; node.polygonOffsetUnits = -2;
      cache.set(source, node);
      return node;
    };
    mesh.material = Array.isArray(mesh.material) ? mesh.material.map(style) : style(mesh.material);
  });
}
