import { wetSurface } from './weather/surfaces';
// A single shared atlas supplies upper-storey bays and a separate row at the building base.
import * as THREE from 'three/webgpu';
import { color, float, floor, fract, mix, normalMap, positionWorld, round, step, texture, uv, vec2, vec3 } from 'three/tsl';
import { programLayout, programPigment } from './facadeProgram';
import { draftWindows, facadeKey, kitGlassMask, quantise, wallFrame, windowEmission } from './nightLights';
import { facadeAttribute, interleaveProgram } from './facadeProgramAttributes';
import type { SharedMaterials } from './sharedMaterials';

export function facadeKitMaterial(source: THREE.Material, renderer: THREE.WebGPURenderer): THREE.MeshStandardNodeMaterial | null {
  const p = source.userData.facade_kit as { version?: number; module?: number; wall?: string; trim?: string } | undefined;
  const original = source as THREE.MeshStandardMaterial;
  if (!p || !original.map || !original.normalMap || !original.roughnessMap) return null;
  if ((source as THREE.NodeMaterial).isNodeMaterial) return source as THREE.MeshStandardNodeMaterial;
  const material = renderer.library.fromMaterial(source.clone()) as THREE.MeshStandardNodeMaterial;
  // GLTFLoader exposes custom semantics in lowercase on both renderer backends.
  // Retain v1 support for published tiles until their next rebuild.
  const layout = (p.version ?? 1) >= 2 ? facadeAttribute('_kit_layout', 'vec3') : vec3(p.module!, 1, 1);
  const origin = p.version === 4 ? facadeAttribute('_kit_extent', 'vec4').xy
    : (p.version ?? 1) >= 2 ? facadeAttribute('_kit_origin', 'vec2') : vec2(0);
  const metres = uv().sub(origin);
  const floors = (p.version ?? 1) >= 3 ? facadeAttribute('_kit_floors', 'vec3') : vec3(layout.z, layout.z, 0);
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
  const wallColour = (p.version ?? 1) >= 2 ? facadeAttribute('_kit_wall', 'vec3') : color(p.wall!);
  const trim = (p.version ?? 1) >= 2 ? facadeAttribute('_kit_trim', 'vec3') : color(p.trim!);
  const paint = program ? programPigment(metres, bays, upper, layout, wallColour.rgb, trim.rgb, program) : null;
  const wall = paint?.pigment ?? wallColour;
  const tint = (p.version ?? 1) >= 3
    ? vec3(1).add(wall.sub(1).mul(float(1).sub(sample.a.mul(2)).clamp(0, 1))).add(trim.sub(1).mul(sample.a.mul(2).sub(1).clamp(0, 1)))
    : mix(mix(vec3(1), trim, step(0.25, albedo.r)), wall, step(0.72, albedo.r));
  const facadeColour = paint ? mix(albedo.mul(tint).mul(float(1).sub(paint.balcony)), paint.trim, paint.band) : albedo.mul(tint);
  const opening = program && paint ? program.inside.mul(float(1).sub(paint.band)) : float(0);
  // Physical frame bars stay centimetres wide even on a 20 m ribbon or a tall
  // lancet. The old 128 px window bitmap remains only on legacy kit versions.
  const glazingColour = program ? mix(trim.mul(.9),
    vec3(.016, .028, .04).mul(mix(.8, 1.35, program.rise)), program.glass) : albedo.mul(tint);
  material.colorNode = mix(facadeColour, glazingColour, opening);
  const normal = texture(original.normalMap, coord).grad(dx, dy).xyz;
  material.normalNode = normalMap(mix(paint ? mix(normal, vec3(.5, .5, 1), paint.band) : normal,
    vec3(.5, .5, 1), opening), vec2(0.65));
  material.roughnessNode = mix(paint ? mix(orm.g, float(.85), paint.band) : orm.g,
    program ? mix(.65, .1, program.glass) : float(.65), opening);
  material.metalnessNode = mix(paint ? mix(orm.b, float(0), paint.band) : orm.b, float(0), opening);
  material.aoNode = mix(orm.r, float(.85), opening);
  // Night windows: kit glass, one cell per bay and storey. The wall's own constants (exact per triangle) name it;
  // v1 has none, so its world-space wall plane does.
  const frame = wallFrame();
  // The unclipped world opening origin survives passage/roof splits and pigment
  // edits. Round at quarter-metre precision to suppress interpolant roundoff.
  const identity = p.version === 4 ? facadeKey([
    round(frame.along.sub(metres.x).mul(4)), round(positionWorld.y.sub(metres.y).mul(4)),
    quantise(layout.y, 4096), quantise(layout.z, 4096)]).bitXor(frame.plane)
    : (p.version ?? 1) >= 2 ? facadeKey([quantise(origin.x, 64), quantise(origin.y, 64),
      quantise(layout.y, 4096), quantise(layout.z, 4096)]) : frame.plane;
  const glazing = program && paint ? program.glass.mul(float(1).sub(paint.band)).mul(float(1).sub(paint.balcony))
    : kitGlassMask(orm.g).mul(float(module.lessThan(42)));
  // Glass modules (atlas row 5) are floor-to-ceiling panes: curtain walls and shopfronts.
  const pane = float(layout.x.lessThan(42).and(layout.x.div(6).floor().equal(5)));
  material.emissiveNode = windowEmission(identity, program?.column ?? floor(bays.x), program?.row ?? floor(bays.y),
    program?.rise ?? fract(bays.y), glazing, pane);
  wetSurface(material, true);
  return material;
}

/** A building wall (not a roof) by its glTF node extras: only these get non-kit night windows. A batched mesh
 *  names its surfaces per object range: a batch of roof ranges is a roof (ortho roofs would otherwise light dark
 *  roof pixels as windows on the world grid). */
export function buildingWall(mesh: THREE.Object3D) {
  for (let node: THREE.Object3D | null = mesh; node; node = node.parent) {
    const data = node.userData;
    const objects = Array.isArray(data.objects) ? data.objects as { surface?: unknown }[] : null;
    if (data.surface === 'roof' || (objects?.length && objects.every(row => row.surface === 'roof'))) return false;
    if (data.layer) return data.layer === 'buildings' || data.layer === 'buildings_outer';
  }
  return false;
}

/** Bind draft facade materials and draw just in front of coplanar published terrain. */
export function tintDraft(model: THREE.Object3D, renderer: THREE.WebGPURenderer, shared?: SharedMaterials) {
  const cache = new Map<THREE.Material, Map<boolean, THREE.Material>>();
  model.traverse(o => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    interleaveProgram(mesh.geometry);
    mesh.castShadow = true; mesh.receiveShadow = true;
    const wall = buildingWall(mesh);
    const style = (source: THREE.Material) => {
      const cached = cache.get(source)?.get(wall);
      if (cached) return cached;
      const pbr = source as THREE.MeshStandardMaterial;
      let node: THREE.MeshStandardMaterial | THREE.MeshStandardNodeMaterial;
      if (pbr.type === 'MeshStandardMaterial' &&
          !Object.values(pbr).some(value => value?.isTexture)) {
        // Plain car/tree/prop colours need no per-tile node graph. LOD can still combine bark/crown.
        node = pbr;
      } else {
        const create = () => {
          const kit = facadeKitMaterial(source, renderer);
          const material = kit ?? renderer.library.fromMaterial(source.clone()) as THREE.MeshStandardNodeMaterial;
          if (!kit && wall && material.isMeshStandardNodeMaterial) draftWindows(material);
          material.polygonOffset = true; material.polygonOffsetFactor = -1; material.polygonOffsetUnits = -2;
          return material;
        };
        node = ((source as THREE.NodeMaterial).isNodeMaterial ? source
          : shared ? shared.styled.acquire(source, wall ? 'draft-wall' : 'draft', model, create) : create()) as THREE.MeshStandardNodeMaterial;
      }
      node.polygonOffset = true; node.polygonOffsetFactor = -1; node.polygonOffsetUnits = -2;
      if (shared) node = shared.acquire(node, model) as typeof node;
      if (!cache.has(source)) cache.set(source, new Map());
      cache.get(source)!.set(wall, node);
      return node;
    };
    mesh.material = Array.isArray(mesh.material) ? mesh.material.map(style) : style(mesh.material);
  });
}
