import * as THREE from 'three/webgpu';
import { QUALITY, type QualityTier } from '../render/quality';

/** Metadata lives on the glTF node; loaders may put the InstancedMesh below it. */
export function vegetationOwner(object: THREE.Object3D): THREE.Object3D | null {
  for (let node: THREE.Object3D | null = object; node; node = node.parent) {
    if (node.userData.layer === 'vegetation') return node;
  }
  return null;
}

/** Vertex clustering preserves the prototype's shape without storing another asset. */
export function clusteredTree(source: THREE.BufferGeometry): THREE.BufferGeometry {
  const positions = source.getAttribute('position');
  const points: number[] = [], remap: number[] = [], cells = new Map<string, number>();
  for (let i = 0; i < positions.count; i++) {
    const x = positions.getX(i), y = positions.getY(i), z = positions.getZ(i);
    const key = `${Math.round(x * 4)},${Math.round(y * 8)},${Math.round(z * 4)}`;
    let index = cells.get(key);
    if (index === undefined) { index = points.length / 3; cells.set(key, index); points.push(x, y, z); }
    remap.push(index);
  }
  const indices: number[] = [], seen = new Set<string>();
  const count = source.index?.count ?? positions.count;
  for (let i = 0; i < count; i += 3) {
    const face = [0, 1, 2].map(j => remap[source.index?.getX(i + j) ?? i + j]);
    if (new Set(face).size < 3) continue;
    const key = [...face].sort((a, b) => a - b).join(',');
    if (!seen.has(key)) { seen.add(key); indices.push(...face); }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
  geometry.setIndex(indices); geometry.computeVertexNormals(); geometry.computeBoundingSphere();
  return geometry;
}

/** Opaque crossed silhouette strips sampled from this prototype: no alpha-test overdraw. */
export function crossedTree(source: THREE.BufferGeometry): THREE.BufferGeometry {
  source.computeBoundingBox();
  const box = source.boundingBox!, p = source.getAttribute('position');
  const positions: number[] = [], indices: number[] = [];
  const levels = 8, widths = new Array<number>(levels + 1).fill(0);
  for (let i = 0; i < p.count; i++) {
    const band = Math.min(levels, Math.round((p.getY(i) - box.min.y) / Math.max(0.001, box.max.y - box.min.y) * levels));
    widths[band] = Math.max(widths[band], Math.hypot(p.getX(i), p.getZ(i)));
  }
  for (let axis = 0; axis < 2; axis++) {
    const offset = positions.length / 3;
    for (let i = 0; i <= levels; i++) {
      const y = THREE.MathUtils.lerp(box.min.y, box.max.y, i / levels);
      const width = widths[i] || Math.max(widths[Math.max(0, i - 1)], widths[Math.min(levels, i + 1)]);
      for (const sign of [-1, 1]) positions.push(axis ? 0 : sign * width, y, axis ? sign * width : 0);
      if (i) { const a = offset + (i - 1) * 2; indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(indices); geometry.computeVertexNormals(); geometry.computeBoundingSphere();
  return geometry;
}

interface Bucket {
  draw: Draw;
  bounds: THREE.Sphere;
  matrices: Float32Array;
  objectIds: unknown[];
  distance: number;
  count: number;
  index: number;
}
interface Draw {
  root: THREE.Group;
  levels: THREE.InstancedMesh[];
  foliage: boolean;
  selections: { bucket: Bucket; count: number }[][];
  signatures: string[];
}
interface Model {
  sources: { mesh: THREE.InstancedMesh; visible: boolean; shadow: boolean }[];
  draws: Draw[];
  buckets: Bucket[];
  geometries: THREE.BufferGeometry[];
  materials: THREE.Material[];
}

/** One shared per-frame instance budget for published and draft models, on either backend. */
export class VegetationLod {
  private models = new Map<THREE.Object3D, Model>();
  private frustum = new THREE.Frustum();
  private projection = new THREE.Matrix4();
  private sphere = new THREE.Sphere();
  private position = new THREE.Vector3();
  private visible: Bucket[] = [];
  constructor(private tier: QualityTier) {}

  add(model: THREE.Object3D) {
    if (this.models.has(model)) return;
    const data: Model = { sources: [], draws: [], buckets: [], geometries: [], materials: [] };
    // Collect before adding children: traversal must never revisit generated meshes.
    model.traverse(object => {
      const mesh = object as THREE.InstancedMesh;
      if (mesh.isInstancedMesh && vegetationOwner(mesh)) data.sources.push({ mesh, visible: mesh.visible, shadow: mesh.castShadow });
    });
    if (!data.sources.length) return;
    this.models.set(model, data);
    const matrix = new THREE.Matrix4(), sphere = new THREE.Sphere();
    for (const { mesh } of data.sources) {
      const owner = vegetationOwner(mesh)!;
      const foliage = owner.userData.material_key !== 'tree_bark';
      const mid = clusteredTree(mesh.geometry);
      const far = foliage ? crossedTree(mesh.geometry) : null;
      data.geometries.push(mid); if (far) data.geometries.push(far);
      const farMaterial = far ? (Array.isArray(mesh.material) ? mesh.material[0] : mesh.material).clone() : null;
      if (farMaterial) { farMaterial.side = THREE.DoubleSide; data.materials.push(farMaterial); }
      const cells = new Map<string, number[]>();
      for (let i = 0; i < mesh.count; i++) {
        mesh.getMatrixAt(i, matrix);
        const key = `${Math.floor(matrix.elements[12] / 50)},${Math.floor(matrix.elements[14] / 50)}`;
        if (!cells.has(key)) cells.set(key, []);
        cells.get(key)!.push(i);
      }
      mesh.visible = false; mesh.castShadow = false;
      const root = new THREE.Group();
      root.name = 'vegetation-lod'; root.userData.vegetationLod = true;
      root.position.copy(mesh.position); root.quaternion.copy(mesh.quaternion); root.scale.copy(mesh.scale);
      const geometries = far ? [mesh.geometry, mid, far] : [mesh.geometry, mid];
      const levels = geometries.map((geometry, level) => {
        const batch = new THREE.InstancedMesh(geometry, level === 2 ? farMaterial! : mesh.material, mesh.count);
        batch.userData = { ...owner.userData, vegetationLod: true };
        batch.userData.instance_object_ids = [];
        batch.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
        batch.receiveShadow = level !== 2; batch.castShadow = level === 0;
        batch.visible = false;
        root.add(batch);
        return batch;
      });
      mesh.parent!.add(root);
      const draw: Draw = { root, levels, foliage, selections: [[], [], []], signatures: ['', '', ''] };
      data.draws.push(draw);
      mesh.geometry.computeBoundingSphere();
      const all = new THREE.Sphere().makeEmpty();
      for (const ids of cells.values()) {
        const bounds = new THREE.Sphere().makeEmpty();
        const matrices = new Float32Array(ids.length * 16);
        ids.forEach((id, i) => {
          mesh.getMatrixAt(id, matrix); matrix.toArray(matrices, i * 16);
          sphere.copy(mesh.geometry.boundingSphere!).applyMatrix4(matrix);
          bounds.union(sphere);
        });
        all.union(bounds);
        data.buckets.push({ draw, matrices, objectIds: ids.map(id => owner.userData.instance_object_ids?.[id]), bounds, distance: 0, count: ids.length, index: data.buckets.length });
      }
      // Static conservative bounds cover every possible packed selection and all cheaper LODs.
      for (const batch of levels) batch.boundingSphere = all.clone();
    }
  }

  update(camera: THREE.PerspectiveCamera) {
    camera.updateMatrixWorld();
    this.projection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    this.frustum.setFromProjectionMatrix(this.projection, camera.coordinateSystem);
    camera.getWorldPosition(this.position);
    const q = QUALITY[this.tier].vegetation;
    this.visible.length = 0;
    for (const [model, data] of this.models) {
      let active = true;
      for (let node: THREE.Object3D | null = model; node; node = node.parent) if (!node.visible) active = false;
      // Inactive tiles can be cached with a parent pointer but absent from the scene's children.
      if (!model.parent || !model.parent.children.includes(model)) active = false;
      for (const draw of data.draws) {
        for (const selection of draw.selections) selection.length = 0;
        for (const batch of draw.levels) batch.visible = false;
        if (active) draw.root.updateWorldMatrix(true, false);
      }
      if (!active) continue;
      for (const bucket of data.buckets) {
        this.sphere.copy(bucket.bounds).applyMatrix4(bucket.draw.root.matrixWorld);
        bucket.distance = Math.max(0, this.position.distanceTo(this.sphere.center) - this.sphere.radius);
        if (bucket.distance <= q.far && this.frustum.intersectsSphere(this.sphere)) this.visible.push(bucket);
      }
    }
    this.visible.sort((a, b) => a.distance - b.distance);
    const remaining = [q.nearInstances, q.midInstances, q.farInstances];
    for (const bucket of this.visible) {
      let level = bucket.distance < q.near ? 0 : bucket.distance < q.mid ? 1 : 2;
      while (level < 3 && remaining[level] <= 0) level++;
      if (level > 2 || (level === 2 && !bucket.draw.foliage)) continue;
      const count = Math.min(bucket.count, remaining[level]);
      remaining[level] -= count;
      bucket.draw.selections[level].push({ bucket, count });
    }
    // Cells cull independently, but compact into one draw per tile/prototype/LOD.
    // Only changed cell selections upload matrices; stationary views do no buffer writes.
    for (const data of this.models.values()) for (const draw of data.draws) {
      draw.levels.forEach((batch, level) => {
        const selections = draw.selections[level];
        if (!selections.length) return;
        const signature = selections.map(s => `${s.bucket.index}:${s.count}`).join(',');
        if (signature !== draw.signatures[level]) {
          const target = batch.instanceMatrix.array as Float32Array;
          let offset = 0;
          const objectIds: unknown[] = [];
          for (const { bucket, count } of selections) {
            target.set(bucket.matrices.subarray(0, count * 16), offset * 16); offset += count;
            objectIds.push(...bucket.objectIds.slice(0, count));
          }
          batch.count = offset;
          batch.userData.instance_object_ids = objectIds;
          batch.instanceMatrix.needsUpdate = true;
          draw.signatures[level] = signature;
        }
        batch.visible = batch.count > 0;
      });
    }
  }

  remove(model: THREE.Object3D) {
    const data = this.models.get(model);
    if (!data) return;
    for (const draw of data.draws) {
      draw.root.removeFromParent();
      for (const mesh of draw.levels) mesh.dispose();
    }
    for (const source of data.sources) { source.mesh.visible = source.visible; source.mesh.castShadow = source.shadow; }
    for (const geometry of data.geometries) geometry.dispose();
    for (const material of data.materials) material.dispose();
    this.models.delete(model);
  }
}
