// Editable placements of the drawn tiles, resolved to the generator's source placement: street furniture rows of the
// merged props batches (index ranges with the generator's object ids), tree instances of VegetationLod (instance ids)
// and street-light posts of StreetLights (post records). Read-only: previews live in preview.ts.
import * as THREE from 'three/webgpu';
import type { Lamp } from '../../render/lights/lampField';

export type EditCategory = 'prop' | 'tree' | 'lamp';
export interface V3 { x: number; y: number; z: number }

/** A prop row inside a merged batch mesh: indices [start, start + count). */
export interface PropRef { kind: 'prop'; mesh: THREE.Mesh; start: number; count: number; row: Record<string, unknown>; rowIndex: number }
export interface TreeRef { kind: 'tree'; asset: string; matrix: THREE.Matrix4 }
export interface LampRef { kind: 'lamp'; lamp: Lamp; index: number }

export interface Editable {
  /** Stable across reloads and passes of the same generation: category, tile and source placement id. */
  key: string;
  category: EditCategory;
  /** prop_type, tree asset or 'street_lamp'. */
  type: string;
  tile: string;
  /** The generator's placement id (register `<layer>:<feature>`, `osm:<id>`, `lidar:<x>:<z>`, `area:…`), or for posts
   *  without one `post:<x10>,<z10>` (tileLights.ts lampKey). */
  objectId: string;
  /** Ground contact point (world, engine metres). */
  base: V3;
  height: number;
  /** Horizontal pick radius (m). */
  radius: number;
  /** Original yaw (deg, three.js rotation.y) and where it came from. */
  yawDeg: number | null;
  yawSource: 'record' | 'instance' | 'estimate-axis' | 'unknown';
  ref: PropRef | TreeRef | LampRef;
}

const DEG = 180 / Math.PI;
const tileOf = (x: number, z: number) => `t0_${Math.floor(x / 250)}_${Math.floor(z / 250)}`;

/** Principal horizontal axis of points (yaw of the long side, deg in [0, 180)); null for near-round footprints. */
export function principalYaw(xs: ArrayLike<number>, zs: ArrayLike<number>): number | null {
  const n = xs.length;
  if (n < 3) return null;
  let mx = 0, mz = 0;
  for (let i = 0; i < n; i++) { mx += xs[i]; mz += zs[i]; }
  mx /= n; mz /= n;
  let sxx = 0, szz = 0, sxz = 0;
  for (let i = 0; i < n; i++) { const dx = xs[i] - mx, dz = zs[i] - mz; sxx += dx * dx; szz += dz * dz; sxz += dx * dz; }
  const spread = Math.hypot(sxx - szz, 2 * sxz);
  if (spread < 0.15 * (sxx + szz)) return null;
  // Long axis direction (cos a, sin a) in x/z; three.js yaw turns +X toward -Z, so yaw = -a.
  const a = 0.5 * Math.atan2(2 * sxz, sxx - szz);
  let yaw = -a * DEG;
  yaw = ((yaw % 180) + 180) % 180;
  return Math.round(yaw * 10) / 10;
}

/** Street furniture of one tile model: every prop row of its props-layer batches (and single prop nodes). */
export function scanProps(model: THREE.Object3D): Editable[] {
  const out: Editable[] = [];
  model.updateMatrixWorld(true);
  const p = new THREE.Vector3();
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh || (mesh as THREE.InstancedMesh).isInstancedMesh || mesh.userData.layer !== 'props' || mesh.userData.shadowBatch) return;
    const geometry = mesh.geometry, index = geometry.index, position = geometry.getAttribute('position');
    if (!position || !position.count) return;
    const total = index ? index.count : position.count;
    const rows = Array.isArray(mesh.userData.objects) ? mesh.userData.objects as Record<string, unknown>[]
      : mesh.userData.prop_type ? [{ ...mesh.userData, start: 0, count: total }] : [];
    rows.forEach((row, rowIndex) => {
      const start = Number(row.start), count = Number(row.count);
      const objectId = String(row.object_id ?? row.id ?? '');
      if (!objectId || !row.prop_type || !Number.isInteger(start) || !Number.isInteger(count) || count <= 0 || start + count > total) return;
      const seen = new Set<number>(), xs: number[] = [], zs: number[] = [];
      let minY = Infinity, maxY = -Infinity, minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
      for (let i = start; i < start + count; i++) {
        const v = index ? index.getX(i) : i;
        if (seen.has(v)) continue;
        seen.add(v);
        p.fromBufferAttribute(position, v).applyMatrix4(mesh.matrixWorld);
        if (!Number.isFinite(p.x + p.y + p.z)) continue;
        xs.push(p.x); zs.push(p.z);
        minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x); minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
        minZ = Math.min(minZ, p.z); maxZ = Math.max(maxZ, p.z);
      }
      if (!xs.length) return;
      const recorded = Array.isArray(row.pole_base) && row.pole_base.length === 3 ? row.pole_base as number[] : null;
      const base = recorded ? model.localToWorld(new THREE.Vector3(recorded[0], recorded[1], recorded[2]))
        : new THREE.Vector3((minX + maxX) / 2, minY, (minZ + maxZ) / 2);
      const tile = String(row.tile ?? mesh.userData.tile ?? tileOf(base.x, base.z));
      const yaw = principalYaw(xs, zs);
      out.push({ key: `prop:${tile}:${objectId}`, category: 'prop', type: String(row.prop_type), tile, objectId,
        base: { x: base.x, y: base.y, z: base.z }, height: Math.max(0.2, maxY - minY),
        radius: Math.min(3, Math.max(0.35, Math.hypot(maxX - minX, maxZ - minZ) / 2)),
        yawDeg: yaw, yawSource: yaw === null ? 'unknown' : 'estimate-axis',
        ref: { kind: 'prop', mesh, start, count, row, rowIndex } });
    });
  });
  return out;
}

/** One tree instance (VegetationLod.forEachTree) as an editable. */
export function treeEditable(tree: { id: string; asset: string; matrix: THREE.Matrix4; height: number; radius: number }): Editable {
  const e = tree.matrix.elements;
  const x = e[12], y = e[13], z = e[14];
  // Yaw of the instance's local +X axis (three.js rotation.y convention).
  const yaw = Math.atan2(-e[2], e[0]) * DEG;
  const tile = tileOf(x, z);
  return { key: `tree:${tile}:${tree.id}`, category: 'tree', type: tree.asset, tile, objectId: tree.id,
    base: { x, y, z }, height: Math.max(1, tree.height), radius: Math.min(2.5, Math.max(0.6, tree.radius * 0.35)),
    yawDeg: Math.round(yaw * 10) / 10, yawSource: 'instance', ref: { kind: 'tree', asset: tree.asset, matrix: tree.matrix.clone() } };
}

/** One street-light post (StreetLights.forEachPost) as an editable. Posts carry no register id (EDIT-FORMAT.md). */
export function lampEditable(tile: string, lamp: Lamp, index: number): Editable {
  return { key: `lamp:${tile}:post:${lamp.key}`, category: 'lamp', type: 'street_lamp', tile, objectId: `post:${lamp.key}`,
    base: { x: lamp.x, y: lamp.y, z: lamp.z }, height: 8 * lamp.scale, radius: 0.45,
    yawDeg: Math.round(lamp.yaw * DEG * 10) / 10, yawSource: 'record', ref: { kind: 'lamp', lamp, index } };
}

export interface TreeSource {
  forEachTree(visit: (tree: { id: string; asset: string; matrix: THREE.Matrix4; height: number; radius: number }) => void,
    near?: { x: number; z: number; radius: number }): void;
}
export interface LampSource { forEachPost(visit: (tile: string, lamp: Lamp, index: number) => void): void }

/** Editables near the camera: props indexed per model on first use (tile loads cost nothing while the editor is
 *  unused), trees and posts re-read at most every 500 ms. */
export class EditableCatalog {
  private props = new Map<THREE.Object3D, Editable[] | null>();
  private dynamic: Editable[] = [];
  private refreshedAt = -Infinity;
  private at = { x: Infinity, z: Infinity };

  constructor(private trees: () => TreeSource | null, private lamps: () => LampSource | null, readonly reach = 150) {}

  /** Register a tile model (distant scenery models have no editables); `scan` indexes its props now and returns them. */
  addModel(model: THREE.Object3D, scan = false): Editable[] {
    if (model.userData.scenery) return [];
    this.props.set(model, null);
    return scan ? this.scanned(model) : [];
  }
  removeModel(model: THREE.Object3D) { this.props.delete(model); }
  private scanned(model: THREE.Object3D): Editable[] {
    let list = this.props.get(model);
    if (!list) this.props.set(model, list = scanProps(model));
    return list;
  }
  private *propLists() { for (const model of this.props.keys()) yield this.scanned(model); }

  refresh(x: number, z: number, now: number, force = false) {
    if (!force && now - this.refreshedAt < 500 && Math.hypot(x - this.at.x, z - this.at.z) < 20) return;
    this.refreshedAt = now; this.at = { x, z };
    const reach2 = (this.reach + 30) ** 2, out: Editable[] = [];
    this.trees()?.forEachTree(tree => {
      const dx = tree.matrix.elements[12] - x, dz = tree.matrix.elements[14] - z;
      if (dx * dx + dz * dz <= reach2) out.push(treeEditable(tree));
    }, { x, z, radius: this.reach + 30 });
    this.lamps()?.forEachPost((tile, lamp, index) => {
      const dx = lamp.x - x, dz = lamp.z - z;
      if (dx * dx + dz * dz <= reach2) out.push(lampEditable(tile, lamp, index));
    });
    this.dynamic = out;
  }

  find(key: string): Editable | null {
    for (const list of this.propLists()) { const e = list.find(x => x.key === key); if (e) return e; }
    return this.dynamic.find(x => x.key === key) ?? null;
  }

  /** Every editable within `radius` of (x, z). */
  near(x: number, z: number, radius = this.reach): Editable[] {
    const r2 = radius * radius, out: Editable[] = [];
    const take = (e: Editable) => { const dx = e.base.x - x, dz = e.base.z - z; if (dx * dx + dz * dz <= r2) out.push(e); };
    for (const list of this.propLists()) list.forEach(take);
    this.dynamic.forEach(take);
    return out;
  }

  get size() { let n = this.dynamic.length; for (const list of this.propLists()) n += list.length; return n; }
}
