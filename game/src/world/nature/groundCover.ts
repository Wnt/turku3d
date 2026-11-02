import * as THREE from 'three/webgpu';
import type { QualityTier } from '../../render/quality';
import { materialKey } from '../../render/materials';
import type { NatureLibrary } from './library';
import { hashId } from './species';

const CELL = 8, DENSITY = 1.5, TILE_LIMIT = 40000;
const QUALITY = {
  mobile: { distance: 14, instances: 1200 }, low: { distance: 20, instances: 2500 },
  medium: { distance: 32, instances: 6000 }, high: { distance: 45, instances: 12000 },
};
interface Cell { center: THREE.Vector3; matrices: Float32Array; count: number; index: number }
interface Cover { mesh: THREE.InstancedMesh; cells: Cell[]; previous: number[] }

/** Grass comes only from the classified lawn triangles, including their measured height.
 * No screen-space colour guessing: paving, river surfaces and vertical banks cannot sprout grass. */
export function* lawnSamples(model: THREE.Object3D): Generator<void, Map<string, THREE.Vector3[]>> {
  model.updateWorldMatrix(true, true);
  const inverse = model.matrixWorld.clone().invert(), transform = new THREE.Matrix4();
  const meshes: THREE.Mesh[] = [];
  model.traverse(o => {
    const mesh = o as THREE.Mesh;
    if (mesh.isMesh && !(mesh as THREE.InstancedMesh).isInstancedMesh && !Array.isArray(mesh.material)
      && materialKey(mesh, mesh.material) === 'draft_grass') meshes.push(mesh);
  });
  const cells = new Map<string, THREE.Vector3[]>();
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  const ab = new THREE.Vector3(), ac = new THREE.Vector3(), normal = new THREE.Vector3();
  const triangleArea = (mesh: THREE.Mesh, i: number) => {
    const p = mesh.geometry.getAttribute('position'), idx = mesh.geometry.index;
    a.fromBufferAttribute(p, idx?.getX(i) ?? i).applyMatrix4(transform);
    b.fromBufferAttribute(p, idx?.getX(i + 1) ?? i + 1).applyMatrix4(transform);
    c.fromBufferAttribute(p, idx?.getX(i + 2) ?? i + 2).applyMatrix4(transform);
    normal.crossVectors(ab.subVectors(b, a), ac.subVectors(c, a));
    const area2 = normal.length();
    return Math.abs(normal.y) < area2 * .75 || area2 < .001 ? 0 : area2 * .5;
  };
  let lawnArea = 0;
  for (const mesh of meshes) {
    transform.multiplyMatrices(inverse, mesh.matrixWorld);
    const count = mesh.geometry.index?.count ?? mesh.geometry.getAttribute('position').count;
    for (let i = 0; i < count; i += 3) {
      if (i % 300 === 0) yield;
      lawnArea += triangleArea(mesh, i);
    }
  }
  // Spread a bounded tile budget over every lawn, not the first triangles visited.
  const density = Math.min(DENSITY, TILE_LIMIT / Math.max(1, lawnArea));
  let remainder = 0;
  let total = 0;
  for (const mesh of meshes) {
    transform.multiplyMatrices(inverse, mesh.matrixWorld);
    const p = mesh.geometry.getAttribute('position'), idx = mesh.geometry.index;
    let seed = hashId(String(mesh.userData.object_id ?? mesh.name));
    const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
    for (let i = 0; i < (idx?.count ?? p.count); i += 3) {
      if (i % 300 === 0) yield;
      remainder += triangleArea(mesh, i) * density;
      const count = Math.min(TILE_LIMIT - total, Math.floor(remainder));
      remainder -= count;
      for (let n = 0; n < count; n++) {
        if (n % 256 === 0) yield;
        const u = Math.sqrt(random()), v = random();
        const at = a.clone().multiplyScalar(1 - u).addScaledVector(b, u * (1 - v)).addScaledVector(c, u * v);
        const key = `${Math.floor(at.x / CELL)},${Math.floor(at.z / CELL)}`;
        if (!cells.has(key)) cells.set(key, []);
        cells.get(key)!.push(at);
      }
      total += count;
      if (total >= TILE_LIMIT) return cells;
    }
  }
  return cells;
}

/** Six triangles per clump, one shared leaf material and one draw per visible tile.
 * Only nearby cells are uploaded; grass receives shadows but never adds a shadow pass. */
export class GroundCover {
  private models = new Map<THREE.Object3D, Cover>();
  private frustum = new THREE.Frustum();
  private projection = new THREE.Matrix4();
  private sphere = new THREE.Sphere();
  private camera = new THREE.Vector3();

  constructor(private tier: QualityTier) {}

  *add(model: THREE.Object3D, library: NatureLibrary): Generator<void> {
    if (this.models.has(model)) return;
    const samples = yield* lawnSamples(model);
    if (!samples.size) return;
    const asset = library.asset('grass_clump'), part = asset.lods[0].parts[0];
    const limit = QUALITY[this.tier].instances;
    const mesh = new THREE.InstancedMesh(part.geometry, part.material, limit);
    mesh.name = 'lawn-ground-cover'; mesh.userData = { groundCover: true, layer: 'vegetation' };
    mesh.receiveShadow = true; mesh.castShadow = false; mesh.visible = false; mesh.count = 0;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    const matrix = new THREE.Matrix4(), rotation = new THREE.Quaternion(), scale = new THREE.Vector3();
    const axis = new THREE.Vector3(0, 1, 0), bounds = new THREE.Box3(), cells: Cell[] = [];
    for (const [key, points] of samples) {
      yield;
      const matrices = new Float32Array(points.length * 16), center = new THREE.Vector3();
      let seed = hashId(key);
      const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
      for (let i = 0; i < points.length; i++) {
        const at = points[i]; center.add(at); bounds.expandByPoint(at);
        rotation.setFromAxisAngle(axis, random() * Math.PI * 2);
        const width = .6 + random() * .6;
        // Short summer lawn with an uneven silhouette, not waist-high meadow blades.
        scale.set(width, (.11 + random() * .19) / asset.height, width);
        matrix.compose(at, rotation, scale).toArray(matrices, i * 16);
      }
      cells.push({ center: center.divideScalar(points.length), matrices, count: points.length, index: cells.length });
    }
    mesh.boundingSphere = bounds.expandByScalar(1).getBoundingSphere(new THREE.Sphere());
    model.add(mesh); this.models.set(model, { mesh, cells, previous: [] });
  }

  update(camera: THREE.PerspectiveCamera) {
    const quality = QUALITY[this.tier];
    camera.getWorldPosition(this.camera);
    this.projection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    this.frustum.setFromProjectionMatrix(this.projection, camera.coordinateSystem);
    const visible: { cover: Cover; cell: Cell; distance: number }[] = [];
    for (const [model, cover] of this.models) {
      cover.mesh.visible = false;
      let active = !!model.parent?.children.includes(model);
      for (let node: THREE.Object3D | null = model; node; node = node.parent) if (!node.visible) active = false;
      if (!active) continue;
      this.sphere.copy(cover.mesh.boundingSphere!).applyMatrix4(model.matrixWorld);
      if (this.camera.distanceTo(this.sphere.center) > quality.distance + this.sphere.radius) continue;
      for (const cell of cover.cells) {
        this.sphere.set(cell.center, CELL).applyMatrix4(model.matrixWorld);
        const distance = this.camera.distanceTo(this.sphere.center);
        if (distance < quality.distance && this.frustum.intersectsSphere(this.sphere)) visible.push({ cover, cell, distance });
      }
    }
    visible.sort((a, b) => a.distance - b.distance);
    let remaining = quality.instances;
    const selected = new Map<Cover, { cell: Cell; count: number }[]>();
    for (const { cover, cell } of visible) {
      if (!remaining) break;
      const count = Math.min(remaining, cell.count); remaining -= count;
      if (!selected.has(cover)) selected.set(cover, []);
      selected.get(cover)!.push({ cell, count });
    }
    for (const [cover, selection] of selected) {
      selection.sort((a, b) => a.cell.index - b.cell.index);
      const signature = selection.flatMap(s => [s.cell.index, s.count]);
      if (signature.length !== cover.previous.length || signature.some((v, i) => cover.previous[i] !== v)) {
        let offset = 0;
        for (const { cell, count } of selection) {
          (cover.mesh.instanceMatrix.array as Float32Array).set(cell.matrices.subarray(0, count * 16), offset * 16);
          offset += count;
        }
        cover.mesh.count = offset; cover.mesh.instanceMatrix.needsUpdate = true; cover.previous = signature;
      }
      cover.mesh.visible = cover.mesh.count > 0;
    }
  }

  remove(model: THREE.Object3D) {
    const cover = this.models.get(model);
    if (!cover) return;
    cover.mesh.removeFromParent(); cover.mesh.dispose(); this.models.delete(model);
  }
}
