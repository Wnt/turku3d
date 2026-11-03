// Edit previews: visual only, never collision, physics or anything another player sees. A prop row of a merged batch
// is hidden by degenerating its index range in place (other rows keep their offsets) and drawn as a small detached copy
// of just its vertices; trees go through VegetationLod.setEdit and street-light posts through StreetLights.setEdit.
// Collision, breakable-pole bodies and the tile's merged shadow batch stay where the generator put them.
import * as THREE from 'three/webgpu';
import type { Editable, PropRef, V3 } from './catalog';
import type { LampEdit } from '../../render/lights';

/** The previewed state of one placement. Positions are ground contact points (world); `turnDeg` is the yaw change. */
export interface PreviewState {
  key: string; category: Editable['category']; objectId: string; tile: string;
  original: V3; after: V3; turnDeg: number; deleted: boolean;
  /** Original yaw (deg) when known: posts need the absolute yaw. */
  originalYawDeg: number | null;
}

/** World matrix taking the original placement to the previewed one: turn about the original base, then move. */
export function previewMatrix(state: Pick<PreviewState, 'original' | 'after' | 'turnDeg'>, out = new THREE.Matrix4()) {
  const o = state.original, a = state.after;
  return out.makeTranslation(a.x, a.y, a.z)
    .multiply(new THREE.Matrix4().makeRotationY(state.turnDeg * Math.PI / 180))
    .multiply(new THREE.Matrix4().makeTranslation(-o.x, -o.y, -o.z));
}

/** A compact copy of the vertices that `vertices` (a triangle list of vertex indices) uses: every attribute,
 *  remapped indices. The source geometry is only read. */
export function rangeGeometry(source: THREE.BufferGeometry, vertices: ArrayLike<number>): THREE.BufferGeometry {
  const count = vertices.length;
  const remap = new Map<number, number>(), order: number[] = [];
  const indices = new Uint32Array(count);
  for (let i = 0; i < count; i++) {
    const v = vertices[i];
    let n = remap.get(v);
    if (n === undefined) { n = order.length; remap.set(v, n); order.push(v); }
    indices[i] = n;
  }
  const out = new THREE.BufferGeometry();
  for (const [name, attribute] of Object.entries(source.attributes)) {
    const a = attribute as THREE.BufferAttribute;
    const interleaved = (attribute as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute;
    const Array = (interleaved ? (attribute as THREE.InterleavedBufferAttribute).data.array : a.array).constructor as Float32ArrayConstructor;
    const copy = new THREE.BufferAttribute(new Array(order.length * a.itemSize), a.itemSize, a.normalized);
    order.forEach((v, i) => {
      copy.setX(i, a.getX(v));
      if (a.itemSize > 1) copy.setY(i, a.getY(v));
      if (a.itemSize > 2) copy.setZ(i, a.getZ(v));
      if (a.itemSize > 3) copy.setW(i, a.getW(v));
    });
    out.setAttribute(name, copy);
  }
  out.setIndex(new THREE.BufferAttribute(order.length > 65535 ? indices : new Uint16Array(indices), 1));
  out.computeBoundingSphere();
  return out;
}

interface PropPreview { ref: PropRef; saved: ArrayLike<number>; copy: THREE.Mesh | null }

export interface PreviewHosts {
  vegetation: () => { setEdit(id: string, delta: THREE.Matrix4 | null | undefined): void } | null;
  lights: { setEdit(key: string, edit: LampEdit | null | undefined): void } | null;
}

export class Previews {
  readonly states = new Map<string, PreviewState>();
  private props = new Map<string, PropPreview>();

  constructor(private hosts: PreviewHosts) {}

  /** Show `state` (or restore the placement when null). Props apply to `editable` when it is resident. */
  set(key: string, state: PreviewState | null, editable?: Editable) {
    const previous = this.states.get(key);
    if (state) this.states.set(key, state); else this.states.delete(key);
    // Keys are `category:tile:objectId`: a restore needs no resident editable.
    const category = state?.category ?? previous?.category ?? key.split(':')[0];
    const objectId = state?.objectId ?? previous?.objectId ?? editable?.objectId ?? key.split(':').slice(2).join(':');
    if (category === 'tree' && objectId) this.hosts.vegetation()?.setEdit(objectId, state ? (state.deleted ? null : previewMatrix(state)) : undefined);
    else if (category === 'lamp') {
      const lampKey = (state?.objectId ?? objectId ?? '').replace(/^post:/, '');
      this.hosts.lights?.setEdit(lampKey, state ? (state.deleted ? null : { ...state.after, yaw: ((state.originalYawDeg ?? 0) + state.turnDeg) * Math.PI / 180 }) : undefined);
    } else if (category === 'prop') {
      if (editable?.ref.kind === 'prop') this.applyProp(key, editable.ref);
      else if (!state) this.releaseProp(key);
    }
  }

  /** A tile model's props were (re)scanned: apply the open previews to its rows. */
  attach(editables: readonly Editable[]) {
    for (const e of editables) if (e.ref.kind === 'prop' && this.states.has(e.key)) this.applyProp(e.key, e.ref);
  }
  /** A tile model leaves: restore its batches (a cached model may come back) and drop the copies. */
  detach(model: THREE.Object3D) {
    for (const [key, p] of this.props) {
      let inside = false;
      for (let o: THREE.Object3D | null = p.ref.mesh; o && !inside; o = o.parent) inside = o === model;
      if (inside) this.releaseProp(key);
    }
  }

  /** Copies follow their batch's visibility (world/propLod.ts switches furniture off beyond 125 m). */
  update() { for (const p of this.props.values()) if (p.copy) p.copy.visible = p.ref.mesh.visible; }

  clear() {
    for (const key of [...this.states.keys()]) this.set(key, null);
    for (const key of [...this.props.keys()]) this.releaseProp(key);
  }

  private applyProp(key: string, ref: PropRef) {
    const state = this.states.get(key);
    let p = this.props.get(key);
    if (p && p.ref.mesh !== ref.mesh) { this.releaseProp(key); p = undefined; }
    if (!state) { if (p) this.releaseProp(key); return; }
    const index = ref.mesh.geometry.index;
    if (!index) return;
    if (!p) {
      const array = index.array as Uint16Array | Uint32Array;
      p = { ref, saved: array.slice(ref.start, ref.start + ref.count), copy: null };
      // Degenerate triangles keep the batch's draw and every other row's offsets.
      array.fill(array[ref.start], ref.start, ref.start + ref.count);
      index.needsUpdate = true;
      this.props.set(key, p);
    }
    if (state.deleted) { this.dropCopy(p); return; }
    if (!p.copy) {
      // From the saved slice: the batch's own range is degenerate now.
      const compact = rangeGeometry(ref.mesh.geometry, p.saved);
      const groups = ref.mesh.geometry.groups, material = ref.mesh.material;
      const chosen = Array.isArray(material) ? material[groups.find(g => ref.start >= g.start && ref.start < g.start + g.count)?.materialIndex ?? 0] : material;
      const copy = new THREE.Mesh(compact, chosen);
      copy.name = `editor-preview:${key}`;
      copy.userData = { editorPreview: key, layer: 'props' };
      copy.castShadow = ref.mesh.castShadow; copy.receiveShadow = ref.mesh.receiveShadow;
      copy.matrixAutoUpdate = false;
      ref.mesh.parent?.add(copy);
      p.copy = copy;
    }
    const parent = ref.mesh.parent!;
    parent.updateWorldMatrix(true, false); ref.mesh.updateWorldMatrix(false, false);
    const world = previewMatrix(state).multiply(ref.mesh.matrixWorld);
    p.copy.matrix.copy(parent.matrixWorld).invert().multiply(world);
    p.copy.matrixWorld.copy(world);
    p.copy.matrixWorldNeedsUpdate = true;
    p.copy.visible = ref.mesh.visible;
  }

  private dropCopy(p: PropPreview) {
    if (!p.copy) return;
    p.copy.removeFromParent(); p.copy.geometry.dispose(); p.copy = null;
  }

  private releaseProp(key: string) {
    const p = this.props.get(key);
    if (!p) return;
    this.props.delete(key);
    this.dropCopy(p);
    const index = p.ref.mesh.geometry.index;
    if (index) { (index.array as Uint16Array | Uint32Array).set(p.saved, p.ref.start); index.needsUpdate = true; }
  }
}
