import * as THREE from 'three/webgpu';
import type { PoleSpec } from '../physics/protocol';

export interface PoleCandidate {
  spec: PoleSpec;
  node: THREE.Object3D;
  range?: { mesh: THREE.Mesh; start: number; count: number };
}

const pole = (e: Record<string, unknown>) => e.prop_type === 'sign' || e.prop_type === 'bus_stop';

function rangeBase(mesh: THREE.Mesh, start: number, count: number): THREE.Vector3 | null {
  const index = mesh.geometry.getIndex();
  const positions = mesh.geometry.getAttribute('position');
  if (!index || !positions || !Number.isInteger(start) || !Number.isInteger(count)
    || start < 0 || count <= 0 || start + count > index.count) return null;
  let low = Infinity;
  for (let i = start; i < start + count; i++) low = Math.min(low, positions.getY(index.getX(i)));
  let x = 0, z = 0, n = 0;
  const used = new Set<number>();
  for (let i = start; i < start + count; i++) {
    const vertex = index.getX(i);
    if (used.has(vertex) || positions.getY(vertex) > low + 0.001) continue;
    used.add(vertex);
    x += positions.getX(vertex); z += positions.getZ(vertex); n++;
  }
  return n ? mesh.localToWorld(new THREE.Vector3(x / n, low, z / n)) : null;
}

/** Read both per-object nodes and the object ranges retained by the visual packager. */
export function extractPoles(model: THREE.Object3D): PoleCandidate[] {
  const found: PoleCandidate[] = [];
  model.updateMatrixWorld(true);
  model.traverse(node => {
    const mesh = node as THREE.Mesh;
    const entries = Array.isArray(node.userData.objects) ? node.userData.objects as Record<string, unknown>[] : null;
    if (entries && mesh.isMesh) {
      for (const e of entries) {
        if (!pole(e)) continue;
        const id = String(e.pole_id ?? (e.tile && e.object_id ? `${e.tile}:${e.object_id}` : ''));
        const start = Number(e.start), count = Number(e.count);
        const base = rangeBase(mesh, start, count);
        if (!id || !base) continue;
        found.push({ node, range: { mesh, start, count }, spec: {
          id, base: { x: base.x, y: base.y, z: base.z }, yaw: 0, height: Number(e.pole_height) || 2.7,
        } });
      }
    } else if (pole(node.userData)) {
      const e = node.userData;
      const id = String(e.pole_id ?? (e.tile && e.object_id ? `${e.tile}:${e.object_id}` : ''));
      if (!id) return;
      const index = mesh.isMesh ? mesh.geometry.getIndex() : null;
      const base = index ? rangeBase(mesh, 0, index.count) : node.getWorldPosition(new THREE.Vector3());
      if (!base) return;
      found.push({ node, range: index ? { mesh, start: 0, count: index.count } : undefined,
        spec: { id, base: { x: base.x, y: base.y, z: base.z }, yaw: node.rotation.y,
        height: Number(e.pole_height) || 2.7 } });
    }
  });
  return found;
}

/** A standing batched sign stays in its draw call; only a broken sign gets its own movable mesh. */
export class PoleRanges {
  private source = new WeakMap<THREE.Mesh, { indices: number[]; removed: { start: number; count: number }[] }>();

  detach(candidate: PoleCandidate): THREE.Object3D {
    if (!candidate.range) return candidate.node;
    const { mesh, start, count } = candidate.range;
    let entry = this.source.get(mesh);
    if (!entry) {
      entry = { indices: Array.from(mesh.geometry.getIndex()!.array), removed: [] };
      this.source.set(mesh, entry);
    }
    if (entry.removed.some(r => r.start === start)) return candidate.node;
    const geometry = mesh.geometry.clone();
    geometry.setIndex(entry.indices.slice(start, start + count));
    const pivot = new THREE.Group();
    const parent = mesh.parent!;
    const base = parent.worldToLocal(new THREE.Vector3(candidate.spec.base.x, candidate.spec.base.y, candidate.spec.base.z));
    pivot.position.copy(base);
    const broken = new THREE.Mesh(geometry, mesh.material);
    broken.matrixAutoUpdate = false;
    broken.matrix.makeTranslation(-base.x, -base.y, -base.z).multiply(mesh.matrix);
    broken.castShadow = true;
    pivot.add(broken);
    parent.add(pivot);
    entry.removed.push({ start, count });
    const keep: number[] = [];
    for (let i = 0; i < entry.indices.length; i++) if (!entry.removed.some(r => i >= r.start && i < r.start + r.count)) keep.push(entry.indices[i]);
    mesh.geometry.setIndex(keep);
    return pivot;
  }
}
