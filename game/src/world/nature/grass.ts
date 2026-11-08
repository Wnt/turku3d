// Instanced grass on the tiles' grass surfaces, near the camera only (GTA-style: dense clumps that thin out with
// distance). Candidates are sampled once per tile on its grass triangles into 16 m world cells; each frame the cells
// around the camera fill one draw per clump kind, and the material dissolves clumps towards the edge of the range.
// Each clump takes the ambient occlusion its ground carries (the generator's COLOR_1 sky bake, so the canopy shade
// under trees too) as a per-instance factor on its indirect light, the way the ground under it is lit.
import * as THREE from 'three/webgpu';
import { BAKED_AMBIENT_STRENGTH } from '../../render/bakedVisibility';
import type { QualityTier } from '../../render/quality';
import { QUALITY } from '../../render/quality';
import { materialKey, surfaceStyle } from '../../render/materials';
import type { SurfaceRange } from '../../render/surfaceArrays';
import type { NatureLibrary } from './library';
import { tuftGeometry } from './grassBlades';
import { deadwoodGeometry, dwarfShrubGeometry, fernGeometry, herbGeometry } from './groundcover';
import { hashId } from './species';

const CELL = 16;
/** Values per candidate: x, y, z, rotation, scale, ambient. */
const STRIDE = 6;
/** Clump kinds: a continuous carpet of lawn cards; taller tufts and meadow flowers only in patches; rosettes; and
 *  the forest floors' plants: dwarf shrubs, ferns and low herbs. The tufts and the forest plants are opaque geometry
 *  built here (grassBlades.ts, groundcover.ts), the others the library's alpha-tested cards. */
const KINDS = ['grass_lawn', 'grass_clump', 'grass_meadow', 'groundcover_rosette', 'groundcover_shrub', 'groundcover_fern', 'groundcover_herb',
  'groundcover_deadwood'] as const;
const TUFT = 1, SHRUB = 4, FERN = 5, HERB = 6, DEADWOOD = 7;
/** Opaque kinds built on the client (blade tufts, forest-floor plants), drawn with the blade material. */
const BUILT = new Set([TUFT, SHRUB, FERN, HERB, DEADWOOD]);

interface TileGrass { key: string; cells: Map<string, { points: number[]; kinds: number[] }> }

/** Smoothed value noise in [0, 1], contrast-stretched into patches; the same on every client. */
function patchNoise(x: number, z: number) {
  const xi = Math.floor(x), zi = Math.floor(z), fx = x - xi, fz = z - zi;
  const h = (i: number, j: number) => (hashId(`${xi + i},${zi + j}`) % 1000) / 999;
  const sx = fx * fx * (3 - 2 * fx), sz = fz * fz * (3 - 2 * fz);
  const v = (h(0, 0) * (1 - sx) + h(1, 0) * sx) * (1 - sz) + (h(0, 1) * (1 - sx) + h(1, 1) * sx) * sz;
  return Math.min(1, Math.max(0, (v - 0.35) / 0.35));
}

/** Unmown grass surfaces (the generator's meadows.py finish, `draft_grass_meadow`). */
const MEADOW = /meadow/;

/** A meadow candidate's clump kind: tall tufts everywhere, meadow flowers in the patches, few lawn cards. */
function meadowKind(patch: number, pick: number) {
  if (pick < 0.15 + patch * 0.25) return 2;
  if (pick < 0.85) return 1;
  return pick < 0.96 ? 0 : 3;
}

/** Forest floors (the generator's forest_floor.py finishes) and their plants. */
const FOREST = /forest_floor_(conifer|broadleaf|moss)/;
type Floor = 'conifer' | 'broadleaf' | 'moss';
/** Forest-floor plants per square metre, as a share of the tier's grass density: the floor texture carries the
 *  litter, needles and moss between them. */
const FOREST_DENSITY = 0.9;

/** A forest-floor candidate's plant, or -1 for bare floor. Needle duff carries bilberry and lingonberry mats in broad
 *  patches, a little wavy hair-grass and few ferns; broadleaf litter wood anemone and sorrel carpets in patches, ferns
 *  and grass tufts; wet moss ferns, sedge-like tufts and dwarf shrubs (Finnish forest site types: VT/MT heath, OMT
 *  herb-rich grove, spruce mire edges). */
export function forestKind(floor: Floor, patch: number, pick: number) {
  // A fallen branch on 2.5 % of the floor's candidates, about one per 30 m2 on the medium tier.
  if (pick >= 0.975) return DEADWOOD;
  if (floor === 'conifer') {
    if (patch > 0.3) return pick < 0.8 ? SHRUB : pick < 0.88 ? FERN : pick < 0.96 ? TUFT : -1;
    return pick < 0.25 ? SHRUB : pick < 0.33 ? TUFT : pick < 0.36 ? FERN : -1;
  }
  if (floor === 'broadleaf') {
    if (patch > 0.4) return pick < 0.75 ? HERB : pick < 0.85 ? FERN : pick < 0.92 ? TUFT : -1;
    return pick < 0.3 ? HERB : pick < 0.38 ? TUFT : pick < 0.43 ? FERN : -1;
  }
  return pick < 0.35 ? FERN : pick < 0.6 ? TUFT : pick < 0.72 ? SHRUB : pick < 0.8 ? HERB : -1;
}

/** A view of a clump geometry (its vertex buffers shared) with the per-instance `grassInstance` the grass materials
 *  read: x shade, y dryness (-1 fresh blue-green .. 1 yellow; 0 neutral), z the ground's baked ambient (their aoNode),
 *  w the instance scale (blade tufts sink by it). One vec4 instead of instanceColor plus two floats: WebGPU allows 8
 *  vertex buffers per pipeline, and the card clumps' position, normal, uv, colour, wind, instance matrix and the
 *  renderer's own buffer leave room for one. Dispose the view, not the source. */
export function grassGeometry(source: THREE.BufferGeometry, instance: THREE.InstancedBufferAttribute) {
  const geometry = new THREE.BufferGeometry();
  for (const [name, attribute] of Object.entries(source.attributes)) geometry.setAttribute(name, attribute);
  geometry.setIndex(source.index);
  geometry.setAttribute('grassInstance', instance);
  geometry.boundingSphere = source.boundingSphere; geometry.boundingBox = source.boundingBox;
  return geometry;
}

export class GrassField {
  /** World-space draws: add to the scene once. */
  readonly root = new THREE.Group();
  private tiles = new Map<THREE.Object3D, TileGrass>();
  private draws: THREE.InstancedMesh[][] = [];
  private instances: THREE.InstancedBufferAttribute[] = [];
  private tuft = tuftGeometry();
  private plants = new Map<number, THREE.BufferGeometry>([[SHRUB, dwarfShrubGeometry()], [FERN, fernGeometry()], [HERB, herbGeometry()],
    [DEADWOOD, deadwoodGeometry()]]);
  private selection = '';
  private readonly settings: { density: number; range: number; instances: number };
  private matrix = new THREE.Matrix4();
  private quaternion = new THREE.Quaternion();
  private scale = new THREE.Vector3();
  private position = new THREE.Vector3();
  private at = new THREE.Vector3();
  private up = new THREE.Vector3(0, 1, 0);

  constructor(library: NatureLibrary, tier: QualityTier) {
    this.settings = QUALITY[tier].grass;
    this.root.name = 'nature-grass';
    library.materials.grassRange.value.set(this.settings.range * 0.6, this.settings.range);
    for (const [k, name] of KINDS.entries()) {
      const cap = this.settings.instances;
      const matrices = new THREE.InstancedBufferAttribute(new Float32Array(cap * 16), 16);
      const instance = new THREE.InstancedBufferAttribute(new Float32Array(cap * 4), 4);
      this.instances.push(instance);
      const parts = BUILT.has(k) ? [{ geometry: k === TUFT ? this.tuft : this.plants.get(k)!, material: library.materials.grassBlade }]
        : library.asset(name).lods[0].parts.map(part => ({ geometry: part.geometry, material: library.materials.grass }));
      this.draws.push(parts.map(part => {
        const mesh = new THREE.InstancedMesh(grassGeometry(part.geometry, instance), part.material, cap);
        mesh.instanceMatrix = matrices;
        mesh.count = 0; mesh.visible = false; mesh.frustumCulled = false;
        mesh.castShadow = false; mesh.receiveShadow = true;
        mesh.userData = { vegetationLod: true, nature: name };
        this.root.add(mesh);
        return mesh;
      }));
    }
  }

  /** Sample a tile's grass triangles (area-weighted, seeded by the tile's position). */
  *addSteps(model: THREE.Object3D): Generator<void> {
    if (this.tiles.has(model) || this.settings.density <= 0) return;
    const tile: TileGrass = { key: '', cells: new Map() };
    this.tiles.set(model, tile);
    model.updateWorldMatrix(true, true);
    const meshes: { mesh: THREE.Mesh; meadow: boolean; floor: Floor | null; start: number; count: number }[] = [];
    const take = (mesh: THREE.Mesh, key: string | null, start: number, count: number) => {
      const floor = key?.match(FOREST)?.[1] as Floor | undefined;
      if (floor) meshes.push({ mesh, meadow: false, floor, start, count });
      else if (key && surfaceStyle(key) === 'grass') meshes.push({ mesh, meadow: MEADOW.test(key), floor: null, start, count });
    };
    model.traverse(o => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh || (mesh as THREE.InstancedMesh).isInstancedMesh || Array.isArray(mesh.material)) return;
      // A merged ground draw (render/surfaceArrays.ts) holds several surfaces: grow on its grass and floor ranges.
      const ranges = mesh.userData.surfaceKeys as SurfaceRange[] | undefined;
      if (ranges) { for (const { key, start, count } of ranges) take(mesh, key, start, count); return; }
      const geometry = mesh.geometry;
      take(mesh, materialKey(mesh, mesh.material), 0, geometry.index?.count ?? geometry.getAttribute('position')?.count ?? 0);
    });
    const origin = new THREE.Vector3().setFromMatrixPosition(model.matrixWorld);
    tile.key = `${origin.x},${origin.y},${origin.z}:${model.name}`;
    let seed = hashId(`${origin.x.toFixed(1)},${origin.z.toFixed(1)}`) || 1;
    const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
    const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), ab = new THREE.Vector3(), ac = new THREE.Vector3();
    for (const { mesh, meadow, floor, start, count: vertices } of meshes) {
      const position = mesh.geometry.getAttribute('position'), index = mesh.geometry.index;
      const color = mesh.geometry.getAttribute('color_1') as THREE.BufferAttribute | undefined;
      // A committed tile's render-only arrays may already be released (world/releaseArrays.ts): no bake then.
      const baked = color && color.array.length >= color.count * color.itemSize ? color : undefined;
      // bakedAmbientContext's factor: the darker of local and sky visibility, weighted by coverage.
      const occlusion = (i: number) => baked ? 1 + (Math.min(baked.getX(i), baked.getY(i)) - 1) * baked.getZ(i) * BAKED_AMBIENT_STRENGTH : 1;
      const first = Math.floor(start / 3), end = first + Math.floor(vertices / 3);
      for (let t = first; t < end; t++) {
        if ((t - first) % 2000 === 0) yield;
        const ia = index?.getX(t * 3) ?? t * 3, ib = index?.getX(t * 3 + 1) ?? t * 3 + 1, ic = index?.getX(t * 3 + 2) ?? t * 3 + 2;
        a.fromBufferAttribute(position, ia).applyMatrix4(mesh.matrixWorld);
        b.fromBufferAttribute(position, ib).applyMatrix4(mesh.matrixWorld);
        c.fromBufferAttribute(position, ic).applyMatrix4(mesh.matrixWorld);
        const oa = occlusion(ia), ob = occlusion(ib), oc = occlusion(ic);
        const area = ab.subVectors(b, a).cross(ac.subVectors(c, a)).length() / 2;
        // Steep banks keep no grass clumps (their normal is far from up).
        if (area <= 0 || Math.abs(ab.y) / (2 * area) < 0.7) continue;
        // Every candidate is kept (a continuous lawn); a world-space patch noise turns some into taller tufts and
        // meadow flowers, so lawns are not an even polka-dot spread.
        const n = Math.floor(area * this.settings.density * (floor ? FOREST_DENSITY : 1) + random());
        for (let k = 0; k < n; k++) {
          if (k % 256 === 0) yield;
          let u = random(), v = random();
          if (u + v > 1) { u = 1 - u; v = 1 - v; }
          const x = a.x + (b.x - a.x) * u + (c.x - a.x) * v, y = a.y + (b.y - a.y) * u + (c.y - a.y) * v, z = a.z + (b.z - a.z) * u + (c.z - a.z) * v;
          // Forest plants grow in larger patches (bilberry mats, anemone carpets) than lawn tufts.
          const patch = patchNoise(x / (floor ? 11 : 7), z / (floor ? 11 : 7)), pick = random();
          const kind = floor ? forestKind(floor, patch, pick)
            : meadow ? meadowKind(patch, pick) : patch > 0.55 && pick < 0.5 ? (pick < 0.12 ? 2 : 1) : pick > 0.95 ? 3 : 0;
          if (kind < 0) continue;
          const key = `${Math.floor(x / CELL)},${Math.floor(z / CELL)}`;
          let cell = tile.cells.get(key);
          if (!cell) tile.cells.set(key, cell = { points: [], kinds: [] });
          const scale = floor ? (kind === TUFT ? 0.6 : 0.75) + patch * 0.3 + random() * 0.35
            : meadow ? 0.85 + patch * 0.35 + random() * 0.3 : kind ? 0.55 + patch * 0.5 : 0.8 + random() * 0.45;
          cell.points.push(x, y, z, random() * Math.PI * 2, scale, oa + (ob - oa) * u + (oc - oa) * v);
          cell.kinds.push(kind);
        }
      }
    }
    this.selection = '';
  }

  remove(model: THREE.Object3D) {
    if (this.tiles.delete(model)) this.selection = '';
  }

  /** Fill the draws from the cells around the camera; unchanged cell sets do no buffer writes. */
  update(camera: THREE.Camera) {
    const p = camera.getWorldPosition(this.position), range = this.settings.range, reach = Math.ceil(range / CELL);
    const cx = Math.floor(p.x / CELL), cz = Math.floor(p.z / CELL);
    // Selection is cached by this cell, so membership and order must use its centre too.
    const centreX = (cx + .5) * CELL, centreZ = (cz + .5) * CELL;
    // Cached tiles can keep a parent pointer while absent from the scene; only drawn tiles grow grass.
    const active = [...this.tiles].filter(([model]) => {
      for (let node: THREE.Object3D | null = model; node; node = node.parent) if (!node.visible) return false;
      return !!model.parent && model.parent.children.includes(model);
    });
    const key = `${cx},${cz}:${active.map(([model]) => model.id).join(',')}`;
    if (key === this.selection) return;
    this.selection = key;
    const counts = this.draws.map(() => 0);
    const candidates: { cell: { points: number[]; kinds: number[] }; distance: number; key: string }[] = [];
    for (const [, tile] of active) {
      for (let dz = -reach; dz <= reach; dz++) for (let dx = -reach; dx <= reach; dx++) {
        const x = cx + dx, z = cz + dz, cell = tile.cells.get(`${x},${z}`);
        if (!cell) continue;
        const distance = Math.hypot((x + .5) * CELL - centreX, (z + .5) * CELL - centreZ);
        // Include both cell half-diagonals: the camera and blades can each lie at a corner.
        if (distance > range + CELL * Math.SQRT2) continue;
        candidates.push({ cell, distance, key: `${x},${z}:${tile.key}` });
      }
    }
    candidates.sort((a, b) => a.distance - b.distance || a.key.localeCompare(b.key));
    // One shared budget across all four kinds, nearest cells first, independent of tile arrival order.
    let remaining = this.settings.instances;
    for (const { cell } of candidates) {
      if (!remaining) break;
      for (let i = 0; i < cell.kinds.length && remaining; i++) {
        const kind = cell.kinds[i], parts = this.draws[kind], n = counts[kind];
        const o = i * STRIDE, s = cell.points[o + 4];
        this.matrix.compose(this.at.set(cell.points[o], cell.points[o + 1], cell.points[o + 2]),
          this.quaternion.setFromAxisAngle(this.up, cell.points[o + 3]), this.scale.set(s, s, s));
        this.matrix.toArray(parts[0].instanceMatrix.array, n * 16);
        const shade = 0.82 + 0.3 * ((cell.points[o + 3] * 7.13) % 1);
        // Blade tufts also vary in hue, from fresh blue-green to yellower, drier clumps.
        const dry = kind === TUFT ? ((cell.points[o + 3] * 3.71) % 1) * 2 - 1 : BUILT.has(kind) ? ((cell.points[o + 3] * 3.71) % 1) - 0.5 : 0;
        (this.instances[kind].array as Float32Array).set([shade, dry, cell.points[o + 5], s], n * 4);
        counts[kind] = n + 1; remaining--;
      }
    }
    for (const [k, parts] of this.draws.entries()) {
      if (counts[k]) {
        for (const [attribute, width] of [[parts[0].instanceMatrix, 16], [this.instances[k], 4]] as const) {
          attribute.clearUpdateRanges(); attribute.addUpdateRange(0, counts[k] * width);
          attribute.needsUpdate = true;
        }
      }
      for (const part of parts) { part.count = counts[k]; part.visible = counts[k] > 0; }
    }
  }

  dispose() {
    for (const parts of this.draws) for (const part of parts) { part.geometry.dispose(); part.dispose(); }
    this.tuft.dispose();
    for (const geometry of this.plants.values()) geometry.dispose();
    this.root.removeFromParent();
  }
}
