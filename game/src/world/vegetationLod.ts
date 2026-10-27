import * as THREE from 'three/webgpu';
import { QUALITY, type QualityTier } from '../render/quality';

/** Metadata lives on the glTF node; loaders may put the InstancedMesh below it. */
export function vegetationOwner(object: THREE.Object3D): THREE.Object3D | null {
  for (let node: THREE.Object3D | null = object; node; node = node.parent) {
    if (node.userData.layer === 'vegetation') return node;
  }
  return null;
}

/** Opaque untextured parts with identical placements can share vertex colours and one draw. */
function combineParts(sources: THREE.InstancedMesh[]): { mesh: THREE.InstancedMesh; parts: THREE.InstancedMesh[]; farGeometry?: THREE.BufferGeometry }[] {
  const key = (mesh: THREE.InstancedMesh) => {
    const material = mesh.material as THREE.MeshStandardMaterial;
    if (Array.isArray(mesh.material) || !material.isMeshStandardMaterial ||
        (material as THREE.Material & { isNodeMaterial?: boolean }).isNodeMaterial ||
        material.transparent || material.vertexColors ||
        Object.values(material).some(value => value?.isTexture) || !mesh.geometry.getAttribute('normal')) return null;
    const params = material.toJSON() as unknown as Record<string, unknown>;
    for (const name of ['uuid', 'name', 'color', 'metadata', 'userData']) delete params[name];
    return JSON.stringify(params);
  };
  const pending = [...sources], combined: { mesh: THREE.InstancedMesh; parts: THREE.InstancedMesh[]; farGeometry?: THREE.BufferGeometry }[] = [];
  while (pending.length) {
    const first = pending.shift()!, signature = key(first);
    if (!signature) continue;
    first.updateMatrix();
    const parts = [first];
    for (let i = pending.length - 1; i >= 0; i--) {
      const next = pending[i]; next.updateMatrix();
      if (next.parent === first.parent && next.count === first.count && key(next) === signature &&
          next.matrix.equals(first.matrix) && first.instanceMatrix.array.every((v, n) => v === next.instanceMatrix.array[n])) {
        parts.push(next); pending.splice(i, 1);
      }
    }
    if (parts.length < 2) continue;
    const positions: number[] = [], normals: number[] = [], colours: number[] = [], indices: number[] = [];
    for (const part of parts) {
      const p = part.geometry.getAttribute('position'), n = part.geometry.getAttribute('normal');
      const colour = (part.material as THREE.MeshStandardMaterial).color, offset = positions.length / 3;
      for (let i = 0; i < p.count; i++) {
        positions.push(p.getX(i), p.getY(i), p.getZ(i));
        normals.push(n.getX(i), n.getY(i), n.getZ(i));
        colours.push(colour.r, colour.g, colour.b);
      }
      const index = part.geometry.index;
      for (let i = 0; i < (index?.count ?? p.count); i++) indices.push(offset + (index?.getX(i) ?? i));
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colours, 3));
    geometry.setIndex(indices);
    const material = (first.material as THREE.MeshStandardMaterial).clone();
    material.color.setRGB(1, 1, 1); material.vertexColors = true;
    const mesh = new THREE.InstancedMesh(geometry, material, first.count);
    mesh.instanceMatrix.array.set(first.instanceMatrix.array);
    mesh.position.copy(first.position); mesh.quaternion.copy(first.quaternion); mesh.scale.copy(first.scale);
    const foliage = parts.find(part => vegetationOwner(part)!.userData.material_key !== 'tree_bark');
    mesh.userData = { ...vegetationOwner(foliage ?? first)!.userData,
      foliageColor: foliage ? (foliage.material as THREE.MeshStandardMaterial).color.toArray() : undefined };
    first.parent!.add(mesh);
    combined.push({ mesh, parts, farGeometry: foliage?.geometry });
  }
  return combined;
}

/** Vertex clustering preserves the prototype's shape without storing another asset. */
export function clusteredTree(source: THREE.BufferGeometry): THREE.BufferGeometry {
  const positions = source.getAttribute('position');
  const colors = source.getAttribute('color'), mergedColors: number[] = [];
  const points: number[] = [], remap: number[] = [], cells = new Map<string, number>();
  for (let i = 0; i < positions.count; i++) {
    const x = positions.getX(i), y = positions.getY(i), z = positions.getZ(i);
    const key = `${Math.round(x * 4)},${Math.round(y * 8)},${Math.round(z * 4)}:${colors ? `${colors.getX(i)},${colors.getY(i)},${colors.getZ(i)}` : ''}`;
    let index = cells.get(key);
    if (index === undefined) { index = points.length / 3; cells.set(key, index); points.push(x, y, z);
      if (colors) mergedColors.push(colors.getX(i), colors.getY(i), colors.getZ(i));
    }
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
  if (colors) geometry.setAttribute('color', new THREE.Float32BufferAttribute(mergedColors, 3));
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
  mergeFar: boolean;
  selections: { bucket: Bucket; count: number }[][];
  signatures: string[];
}
interface Model {
  combined: THREE.InstancedMesh[];
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
    const data: Model = { combined: [], sources: [], draws: [], buckets: [], geometries: [], materials: [] };
    // Collect before adding children: traversal must never revisit generated meshes.
    model.traverse(object => {
      const mesh = object as THREE.InstancedMesh;
      if (mesh.isInstancedMesh && vegetationOwner(mesh)) data.sources.push({ mesh, visible: mesh.visible, shadow: mesh.castShadow });
    });
    if (!data.sources.length) return;
    this.models.set(model, data);
    const matrix = new THREE.Matrix4(), sphere = new THREE.Sphere();
    const combined = combineParts(data.sources.map(source => source.mesh));
    data.combined = combined.map(part => part.mesh);
    const replaced = new Set(combined.flatMap(part => part.parts));
    for (const { mesh } of data.sources) { mesh.visible = false; mesh.castShadow = false; }
    for (const mesh of [...data.sources.map(source => source.mesh).filter(mesh => !replaced.has(mesh)), ...data.combined]) {
      const owner = vegetationOwner(mesh)!;
      if (data.combined.includes(mesh)) {
        data.geometries.push(mesh.geometry); data.materials.push(mesh.material as THREE.Material);
      }
      const foliage = owner.userData.material_key !== 'tree_bark';
      const mid = clusteredTree(mesh.geometry);
      // A clustered crown can be cheaper than the 32-triangle silhouette.
      // Share its mid/far draw rather than add geometry and another draw call.
      const mergeFar = foliage && (mid.index?.count ?? mid.getAttribute('position').count) <= 96;
      const farSource = combined.find(part => part.mesh === mesh)?.farGeometry ?? mesh.geometry;
      const far = foliage && !mergeFar ? crossedTree(farSource) : null;
      data.geometries.push(mid); if (far) data.geometries.push(far);
      const farMaterial = far ? (Array.isArray(mesh.material) ? mesh.material[0] : mesh.material).clone() : null;
      if (farMaterial) {
        if (mesh.userData.foliageColor) {
          (farMaterial as THREE.MeshStandardMaterial).vertexColors = false;
          (farMaterial as THREE.MeshStandardMaterial).color.fromArray(mesh.userData.foliageColor);
        }
        farMaterial.side = THREE.DoubleSide; data.materials.push(farMaterial);
      }
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
      const draw: Draw = { root, levels, foliage, mergeFar, selections: [[], [], []], signatures: ['', '', ''] };
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
      if (draw.mergeFar) draw.selections[1].push(...draw.selections[2]);
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
    for (const mesh of data.combined) { mesh.removeFromParent(); mesh.dispose(); }
    for (const source of data.sources) { source.mesh.visible = source.visible; source.mesh.castShadow = source.shadow; }
    for (const geometry of data.geometries) geometry.dispose();
    for (const material of data.materials) material.dispose();
    this.models.delete(model);
  }
}
