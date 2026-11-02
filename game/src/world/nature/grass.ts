// Instanced grass on the tiles' grass surfaces, near the camera only (GTA-style: dense clumps that thin out with
// distance). Candidates are sampled once per tile on its grass triangles into 16 m world cells; each frame the cells
// around the camera fill one draw per clump kind, and the material dissolves clumps towards the edge of the range.
import * as THREE from 'three/webgpu';
import type { QualityTier } from '../../render/quality';
import { QUALITY } from '../../render/quality';
import { materialKey, surfaceStyle } from '../../render/materials';
import type { NatureLibrary } from './library';
import { hashId } from './species';

const CELL = 16;
/** Clump kinds: a continuous carpet of lawn cards; taller tufts and meadow flowers only in patches; rosettes. */
const KINDS = ['grass_lawn', 'grass_clump', 'grass_meadow', 'groundcover_rosette'] as const;

interface TileGrass { key: string; cells: Map<string, { points: number[]; kinds: number[] }> }

/** Smoothed value noise in [0, 1], contrast-stretched into patches; the same on every client. */
function patchNoise(x: number, z: number) {
  const xi = Math.floor(x), zi = Math.floor(z), fx = x - xi, fz = z - zi;
  const h = (i: number, j: number) => (hashId(`${xi + i},${zi + j}`) % 1000) / 999;
  const sx = fx * fx * (3 - 2 * fx), sz = fz * fz * (3 - 2 * fz);
  const v = (h(0, 0) * (1 - sx) + h(1, 0) * sx) * (1 - sz) + (h(0, 1) * (1 - sx) + h(1, 1) * sx) * sz;
  return Math.min(1, Math.max(0, (v - 0.35) / 0.35));
}

export class GrassField {
  /** World-space draws: add to the scene once. */
  readonly root = new THREE.Group();
  private tiles = new Map<THREE.Object3D, TileGrass>();
  private draws: THREE.InstancedMesh[][] = [];
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
    for (const name of KINDS) {
      const asset = library.asset(name), cap = this.settings.instances;
      const matrices = new THREE.InstancedBufferAttribute(new Float32Array(cap * 16), 16).setUsage(THREE.DynamicDrawUsage);
      const colors = new THREE.InstancedBufferAttribute(new Float32Array(cap * 3), 3).setUsage(THREE.DynamicDrawUsage);
      this.draws.push(asset.lods[0].parts.map(part => {
        const mesh = new THREE.InstancedMesh(part.geometry, library.materials.grass, cap);
        mesh.instanceMatrix = matrices; mesh.instanceColor = colors;
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
    const meshes: THREE.Mesh[] = [];
    model.traverse(o => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh || (mesh as THREE.InstancedMesh).isInstancedMesh || Array.isArray(mesh.material)) return;
      const key = materialKey(mesh, mesh.material);
      if (key && surfaceStyle(key) === 'grass') meshes.push(mesh);
    });
    const origin = new THREE.Vector3().setFromMatrixPosition(model.matrixWorld);
    tile.key = `${origin.x},${origin.y},${origin.z}:${model.name}`;
    let seed = hashId(`${origin.x.toFixed(1)},${origin.z.toFixed(1)}`) || 1;
    const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
    const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), ab = new THREE.Vector3(), ac = new THREE.Vector3();
    for (const mesh of meshes) {
      const position = mesh.geometry.getAttribute('position'), index = mesh.geometry.index;
      const count = (index?.count ?? position.count) / 3;
      for (let t = 0; t < count; t++) {
        if (t % 2000 === 0) yield;
        a.fromBufferAttribute(position, index?.getX(t * 3) ?? t * 3).applyMatrix4(mesh.matrixWorld);
        b.fromBufferAttribute(position, index?.getX(t * 3 + 1) ?? t * 3 + 1).applyMatrix4(mesh.matrixWorld);
        c.fromBufferAttribute(position, index?.getX(t * 3 + 2) ?? t * 3 + 2).applyMatrix4(mesh.matrixWorld);
        const area = ab.subVectors(b, a).cross(ac.subVectors(c, a)).length() / 2;
        // Steep banks keep no grass clumps (their normal is far from up).
        if (area <= 0 || Math.abs(ab.y) / (2 * area) < 0.7) continue;
        // Every candidate is kept (a continuous lawn); a world-space patch noise turns some into taller tufts and
        // meadow flowers, so lawns are not an even polka-dot spread.
        const n = Math.floor(area * this.settings.density + random());
        for (let k = 0; k < n; k++) {
          if (k % 256 === 0) yield;
          let u = random(), v = random();
          if (u + v > 1) { u = 1 - u; v = 1 - v; }
          const x = a.x + (b.x - a.x) * u + (c.x - a.x) * v, y = a.y + (b.y - a.y) * u + (c.y - a.y) * v, z = a.z + (b.z - a.z) * u + (c.z - a.z) * v;
          const patch = patchNoise(x / 7, z / 7), pick = random();
          const kind = patch > 0.55 && pick < 0.5 ? (pick < 0.12 ? 2 : 1) : pick > 0.95 ? 3 : 0;
          const key = `${Math.floor(x / CELL)},${Math.floor(z / CELL)}`;
          let cell = tile.cells.get(key);
          if (!cell) tile.cells.set(key, cell = { points: [], kinds: [] });
          cell.points.push(x, y, z, random() * Math.PI * 2, kind ? 0.55 + patch * 0.5 : 0.8 + random() * 0.45);
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
        const distance = Math.hypot((x + .5) * CELL - p.x, (z + .5) * CELL - p.z);
        if (distance > range + CELL * Math.SQRT1_2) continue;
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
        const o = i * 5, s = cell.points[o + 4];
        this.matrix.compose(this.at.set(cell.points[o], cell.points[o + 1], cell.points[o + 2]),
          this.quaternion.setFromAxisAngle(this.up, cell.points[o + 3]), this.scale.set(s, s, s));
        this.matrix.toArray(parts[0].instanceMatrix.array, n * 16);
        const shade = 0.82 + 0.3 * ((cell.points[o + 3] * 7.13) % 1);
        (parts[0].instanceColor!.array as Float32Array).set([shade, shade, shade * 0.96], n * 3);
        counts[kind] = n + 1; remaining--;
      }
    }
    for (const [k, parts] of this.draws.entries()) {
      parts[0].instanceMatrix.needsUpdate = true; parts[0].instanceColor!.needsUpdate = true;
      for (const part of parts) { part.count = counts[k]; part.visible = counts[k] > 0; }
    }
  }

  dispose() {
    for (const parts of this.draws) for (const part of parts) part.dispose();
    this.root.removeFromParent();
  }
}
