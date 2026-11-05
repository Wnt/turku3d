// Far openings retain the detailed facade's program or baked glazing mask.
import * as THREE from 'three/webgpu';
import { attribute, dot, float, floor, fract, luminance, mix, positionWorld, smoothstep, texture, uv, varying, vec2, vec3, vertexColor } from 'three/tsl';
import { nightUniforms, wallFrame, windowEmission } from './nightLights';
import { facadeProgramSurface } from './facadeProgramSurface';
import { interleaveProgram } from './facadeProgramAttributes';
import type { SharedMaterials } from './sharedMaterials';

const source = new THREE.MeshStandardMaterial({ name: 'distant-city-pigment', roughness: 1, metalness: 0 });
// Node identity participates in the program key. Ground and plain shells have the
// same pigment expression; share it without sharing their material lifetimes.
const sceneryPigment = vertexColor().rgb;
// The far canopy's summer tint, one node for every cell's material (fresh nodes gave each cell its own program).
const sceneryCanopy = vec3(.45, .78, .30).mul(luminance(vertexColor().rgb));
const regionalMasks = new WeakMap<THREE.DataTexture, THREE.Node<'bool'>>();
function regionalMask(coverage: THREE.DataTexture) {
  let mask = regionalMasks.get(coverage);
  if (!mask) regionalMasks.set(coverage, mask = texture(coverage, positionWorld.xz.div(250 * 256).add(.5)).r.lessThan(.5));
  return mask;
}

/** The HD roof photo a block shell's roofs sample, and its world-plane mapping: uv = (u . (x, z, 1), v . (x, z, 1)). */
export interface ShellRoof { map: THREE.Texture; u: readonly [number, number, number]; v: readonly [number, number, number] }

/** The roof pigment of a block shell: its HD children's own roof photo at the same world position (GTA V's LOD
 *  texture is the blurred HD one; here the GPU mip chain does the blurring), so a roof keeps its colour across the
 *  280 m hand-over. Roof faces are the non-wall ones (wallFrame); walls keep their vertex pigment, which the
 *  shell-look migration sets to the HD facade's mean (pipeline/turku3d/migrations/shell_look.py). */
function shellPigment(roof?: ShellRoof) {
  if (!roof) return sceneryPigment;
  const p = vec3(positionWorld.x, positionWorld.z, 1);
  const coord = vec2(dot(p, vec3(...roof.u)), dot(p, vec3(...roof.v)));
  return mix(texture(roof.map, coord).rgb, vertexColor().rgb, wallFrame().wall);
}

/** The procedural windows of a glazed shell wall: the HD kit's own bay, storey and opening size where the shell-look
 *  migration wrote them (`_shell_grid`), else the distant city's 2.6 m x 3.2 m grid with a 0.5 x 0.6 opening. */
function shellWindows(grid: boolean) {
  const frame = wallFrame();
  const g = grid ? varying(attribute('_shell_grid', 'vec4'), 'v_shell_grid').setInterpolation('flat') : null;
  const known = g ? g.x.greaterThan(.5).and(g.y.greaterThan(1)) : null;
  const bayM = g && known ? known.select(g.x, float(2.6)) : float(2.6), storeyM = g && known ? known.select(g.y, float(3.2)) : float(3.2);
  const width = g && known ? known.select(g.z.clamp(.15, .9), float(.5)) : float(.5);
  const height = g && known ? known.select(g.w.clamp(.15, .9), float(.6)) : float(.6);
  const storey = positionWorld.y.div(storeyM), bay = frame.along.div(bayM);
  const across = fract(bay), rise = fract(storey), sill = float(1).sub(height).mul(.45);
  const half = width.div(2), edge = float(.05);
  const glass = smoothstep(float(.5).sub(half).sub(edge), float(.5).sub(half).add(edge), across)
    .mul(smoothstep(float(.5).add(half).add(edge), float(.5).add(half).sub(edge), across))
    .mul(smoothstep(sill.sub(edge), sill.add(edge), rise)).mul(smoothstep(sill.add(height).add(edge), sill.add(height).sub(edge), rise))
    .mul(frame.wall);
  return { frame, glass, bay, storey, rise };
}

/** HD kit wall paint (facadeKit.ts painted band) and a typical roof photo's ORM roughness. */
const SHELL_WALL_ROUGHNESS = .85;
const SHELL_ROOF_ROUGHNESS = float(.9);

/** `shaded`: the geometry carries the HD model's own normals (block LOD shells), so it shades like the HD facade
 *  instead of from screen-space derivatives. */
export function sceneryMaterial(layer: string, coverage?: THREE.DataTexture, map?: THREE.Texture | null, programVersion = 5,
  roof?: ShellRoof, shaded = false) {
  // colorNode owns the authored pigment; vertexColors=true would multiply it a second time.
  const material = new THREE.MeshStandardNodeMaterial({ roughness: 1, metalness: 0 });
  material.colorNode = sceneryPigment;
  material.name = `distant-city-${layer}`;
  material.flatShading = !shaded;
  if (layer === 'regional' && coverage) material.maskNode = regionalMask(coverage);
  // The old cached tree prototypes carry autumn pigments. Match the city's summer canopy,
  // retaining their tonal variation without turning the far skyline into bright orange spikes.
  if (layer === 'vegetation') material.colorNode = sceneryCanopy;
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
  if (layer === 'buildings-lod-glazed' || layer === 'buildings-lod-glazed-grid') {
    // Block LOD walls whose HD facade program has windows (pipeline/turku3d/coarse/blocks.py): a soft window in each
    // bay and storey darkens the pigment by day and lights the same windows at night.
    // The glass and paint of the HD kit (render/facadeKit.ts): dark glazing brightening up each pane, roughness .1 on
    // glass (it mirrors the sky), the kit's painted-band .85 on walls; .8 leaves the kit's frame bars' share.
    const { frame, glass, bay, storey, rise } = shellWindows(layer === 'buildings-lod-glazed-grid');
    material.colorNode = mix(shellPigment(roof), vec3(.016, .028, .04).mul(mix(.8, 1.35, rise)), glass.mul(.8));
    material.roughnessNode = mix(SHELL_ROOF_ROUGHNESS, mix(float(SHELL_WALL_ROUGHNESS), float(.1), glass.mul(.8)), frame.wall);
    material.emissiveNode = windowEmission(frame.plane, floor(bay), floor(storey), rise, glass);
    return material;
  }
  if (layer === 'buildings-lod') {
    if (roof) material.colorNode = shellPigment(roof);
    material.roughnessNode = mix(SHELL_ROOF_ROUGHNESS, float(SHELL_WALL_ROUGHNESS), wallFrame().wall);
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

/** Least-squares world-plane mapping of each roof photo the HD roofs under `owner` use: uv ~ M (x, z, 1) over every
 *  vertex of every roof mesh sharing the map. Orthophoto roofs are planar projections (pipeline ortho_uv), so the fit
 *  is exact; a map whose roofs are not (metal seams, landmark crops) is left out. `owner` sits at the world origin
 *  (tile models are never moved), so these coordinates are positionWorld. Keyed by the HD placement's block id. */
export function shellRoofs(owner: THREE.Object3D): Map<string, ShellRoof> {
  const fits = new Map<THREE.Texture, { n: number; s: number[]; meshes: { block: string; points: number }[]; samples: number[][] }>();
  owner.updateWorldMatrix(true, true);
  const inverse = owner.matrixWorld.clone().invert(), m = new THREE.Matrix4(), v = new THREE.Vector3();
  for (const wrapper of owner.children) {
    const block = wrapper.userData.block as string | undefined;
    if (!block || !wrapper.userData.archetype) continue;
    wrapper.traverse(o => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh || Array.isArray(mesh.material)) return;
      let node: THREE.Object3D | null = mesh, roof = false;
      for (; node && node !== wrapper.parent; node = node.parent) if (node.userData.surface === 'roof') roof = true;
      const map = (mesh.material as THREE.MeshStandardMaterial).map;
      const uvs = mesh.geometry.getAttribute('uv'), pos = mesh.geometry.getAttribute('position');
      if (!roof || !map || !uvs || !pos) return;
      m.multiplyMatrices(inverse, mesh.matrixWorld);
      let fit = fits.get(map);
      if (!fit) fits.set(map, fit = { n: 0, s: [], meshes: [], samples: [] });
      const step = Math.max(1, Math.floor(pos.count / 64));
      for (let i = 0; i < pos.count; i += step) {
        v.fromBufferAttribute(pos, i).applyMatrix4(m);
        fit.samples.push([v.x, v.z, uvs.getX(i), uvs.getY(i)]);
      }
      fit.meshes.push({ block, points: pos.count });
    });
  }
  const out = new Map<string, ShellRoof>(), votes = new Map<string, Map<ShellRoof, number>>();
  for (const [map, fit] of fits) {
    const roof = planeFit(map, fit.samples);
    if (!roof) continue;
    for (const { block, points } of fit.meshes) {
      let tally = votes.get(block);
      if (!tally) votes.set(block, tally = new Map());
      tally.set(roof, (tally.get(roof) ?? 0) + points);
    }
  }
  for (const [block, tally] of votes) out.set(block, [...tally].sort((a, b) => b[1] - a[1])[0][0]);
  return out;
}

/** uv = M (x, z, 1) through the samples, or null when the roofs are not one planar projection (residual > 0.5 %). */
function planeFit(map: THREE.Texture, samples: number[][]): ShellRoof | null {
  if (samples.length < 3) return null;
  const A = [[0, 0, 0], [0, 0, 0], [0, 0, 0]], bu = [0, 0, 0], bv = [0, 0, 0];
  for (const [x, z, u, w] of samples) {
    const r = [x, z, 1];
    for (let i = 0; i < 3; i++) { bu[i] += r[i] * u; bv[i] += r[i] * w; for (let j = 0; j < 3; j++) A[i][j] += r[i] * r[j]; }
  }
  const solve = (b: number[]) => {
    const M = A.map((row, i) => [...row, b[i]]);
    for (let c = 0; c < 3; c++) {
      const p = M.slice(c).reduce((best, row, k) => Math.abs(row[c]) > Math.abs(M[best][c]) ? k + c : best, c);
      [M[c], M[p]] = [M[p], M[c]];
      if (Math.abs(M[c][c]) < 1e-9) return null;
      for (let r = 0; r < 3; r++) if (r !== c) { const f = M[r][c] / M[c][c]; for (let k = c; k < 4; k++) M[r][k] -= f * M[c][k]; }
    }
    return [M[0][3] / M[0][0], M[1][3] / M[1][1], M[2][3] / M[2][2]] as [number, number, number];
  };
  const u = solve(bu), v = solve(bv);
  if (!u || !v) return null;
  for (const [x, z, su, sv] of samples)
    if (Math.abs(u[0] * x + u[1] * z + u[2] - su) > .005 || Math.abs(v[0] * x + v[1] * z + v[2] - sv) > .005) return null;
  return { map, u, v };
}

/** Style a block LOD parent clone; pooled materials are released with `owner` (its tile model). `roof` is the HD
 *  roof photo of the block (shellRoofs), so the shell's roofs keep the HD colours. Wall colours and the window grid
 *  come with the shell itself (shell-look migration). */
export function styleBlockLod(root: THREE.Object3D, owner: THREE.Object3D, shared?: SharedMaterials, roof?: ShellRoof) {
  root.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    const glazed = mesh.userData.lod === 'walls-glazed';
    const layer = !glazed ? 'buildings-lod' : mesh.geometry.hasAttribute('_shell_grid') ? 'buildings-lod-glazed-grid' : 'buildings-lod-glazed';
    const shaded = mesh.geometry.hasAttribute('normal') ? ':shaded' : '';
    const key = roof ? `block-lod${shaded}:${layer}:${roof.map.uuid}:${[...roof.u, ...roof.v].map(x => x.toPrecision(9)).join(',')}` : `block-lod${shaded}:${layer}`;
    const create = () => sceneryMaterial(layer, undefined, null, 5, roof, !!shaded);
    mesh.material = shared ? shared.styled.acquire(lodSource, key, owner, create) : create();
    mesh.castShadow = true; mesh.receiveShadow = true;
  });
}

// Material experiments are applied explicitly to resident scenery through the dev session.
if (import.meta.hot) import.meta.hot.accept();
