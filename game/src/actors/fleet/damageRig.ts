import * as THREE from 'three/webgpu';
import { DamageState, damageLayout, decodeDamage, nodeIndex, type DamageLayout } from '../../vehicle/damage';
import { fleetDamageBox, type FleetModel } from './catalog';

// Body damage on the GPU (vehicle/README.md "Body damage"): a damageable car body is a SkinnedMesh bound to the 54-node
// deformation cage of vehicle/damage.ts. The vertex buffers never change; each car's skeleton carries one rigid
// transform per node (its offset plus the local rotation of the displaced cage), so a wrecked car draws exactly like a
// fresh one: the same draws, vertices and shared fleet material.

const IDENTITY = new THREE.Matrix4();
/** Parts smaller than this (bounding-box diagonal, m) move rigidly with the cage at their centroid: lamps, grille,
 *  mirrors, handles, seats. Bigger connected surfaces (panels, glazing, bumpers) blend per vertex. */
const RIGID_PART_M = .55;
/** The displaced cage's local rotation is clamped, so a crushed corner never spins a lever arm through the body; the
 *  stiff cabin stations (the middle of the car) barely turn, so its roof and pillars stay put. */
const MAX_TILT = .45, CABIN_TILT = .01;

/** Up to four cage nodes and weights for a point: Kuhn tetrahedra of the station × level × lateral cell it lies in
 *  (clamped to the cage), so the weights reproduce any affine offset field exactly and stay continuous across cells. */
export function cageWeights(layout: DamageLayout, x: number, y: number, z: number, index: Uint8Array | number[], weight: Float32Array | number[], at = 0) {
  const { stations, levels, laterals } = layout;
  let s = 0;
  while (s < stations.length - 2 && z > stations[s + 1]) s++;
  let l = 0;
  while (l < laterals.length - 2 && x > laterals[l + 1]) l++;
  const clamp = (v: number) => Math.max(0, Math.min(1, v));
  const t = [clamp((x - laterals[l]) / (laterals[l + 1] - laterals[l])), clamp((y - levels[0]) / (levels[1] - levels[0])),
    clamp((z - stations[s]) / (stations[s + 1] - stations[s]))];
  // Walk from the cell's low corner along the axes in decreasing order of their coordinate.
  const order = [0, 1, 2].sort((a, b) => t[b] - t[a] || a - b);
  const corner = [l, 0, s];
  let previous = 1;
  for (let k = 0; k < 4; k++) {
    const next = k < 3 ? t[order[k]] : 0;
    index[at + k] = nodeIndex(corner[2], corner[1], corner[0]);
    weight[at + k] = previous - next;
    previous = next;
    if (k < 3) corner[order[k]]++;
  }
}

/** Adds `skinIndex`/`skinWeight` for the cage to a fleet body or glazing geometry (once per geometry). */
export function skinToCage(geometry: THREE.BufferGeometry, layout: DamageLayout) {
  if (geometry.getAttribute('skinIndex')) return;
  const position = geometry.getAttribute('position'), n = position.count;
  // Connected parts: shared triangle corners, and coincident positions (hard edges split the welded vertices).
  const parent = new Int32Array(n).map((_, i) => i);
  const find = (i: number): number => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
  const union = (a: number, b: number) => { a = find(a); b = find(b); if (a !== b) parent[a] = b; };
  const index = geometry.index;
  if (index) for (let i = 0; i < index.count; i += 3) { union(index.getX(i), index.getX(i + 1)); union(index.getX(i), index.getX(i + 2)); }
  else for (let i = 0; i < n; i += 3) { union(i, i + 1); union(i, i + 2); }
  const seen = new Map<string, number>();
  for (let i = 0; i < n; i++) {
    const key = `${Math.round(position.getX(i) * 1000)},${Math.round(position.getY(i) * 1000)},${Math.round(position.getZ(i) * 1000)}`;
    const other = seen.get(key);
    if (other === undefined) seen.set(key, i); else union(i, other);
  }
  const parts = new Map<number, { min: number[]; max: number[]; sum: number[]; count: number }>();
  for (let i = 0; i < n; i++) {
    const root = find(i), x = position.getX(i), y = position.getY(i), z = position.getZ(i);
    let part = parts.get(root);
    if (!part) { part = { min: [x, y, z], max: [x, y, z], sum: [0, 0, 0], count: 0 }; parts.set(root, part); }
    part.min = [Math.min(part.min[0], x), Math.min(part.min[1], y), Math.min(part.min[2], z)];
    part.max = [Math.max(part.max[0], x), Math.max(part.max[1], y), Math.max(part.max[2], z)];
    part.sum[0] += x; part.sum[1] += y; part.sum[2] += z; part.count++;
  }
  const rigid = new Map<number, { index: number[]; weight: number[] }>();
  for (const [root, part] of parts) {
    if (Math.hypot(part.max[0] - part.min[0], part.max[1] - part.min[1], part.max[2] - part.min[2]) >= RIGID_PART_M) continue;
    const weights = { index: [0, 0, 0, 0], weight: [0, 0, 0, 0] };
    cageWeights(layout, part.sum[0] / part.count, part.sum[1] / part.count, part.sum[2] / part.count, weights.index, weights.weight);
    rigid.set(root, weights);
  }
  const skinIndex = new Uint8Array(n * 4), skinWeight = new Uint8Array(n * 4), w = new Float32Array(4);
  for (let i = 0; i < n; i++) {
    const part = rigid.get(find(i));
    if (part) { skinIndex.set(part.index, i * 4); w.set(part.weight); }
    else cageWeights(layout, position.getX(i), position.getY(i), position.getZ(i), skinIndex, w, i * 4);
    // Unsigned bytes that sum to exactly 255: the rounding remainder goes to the largest weight.
    let sum = 0, largest = 0;
    for (let k = 0; k < 4; k++) { skinWeight[i * 4 + k] = Math.round(w[k] * 255); sum += skinWeight[i * 4 + k]; if (w[k] > w[largest]) largest = k; }
    skinWeight[i * 4 + largest] += 255 - sum;
  }
  geometry.setAttribute('skinIndex', new THREE.BufferAttribute(skinIndex, 4));
  geometry.setAttribute('skinWeight', new THREE.BufferAttribute(skinWeight, 4, true));
}

/** One car's cage pose: a detached skeleton whose bones are not in the scene; their world matrices are the node
 *  transforms in the body's own (fleet model) frame. */
export class DamageRig {
  readonly skeleton: THREE.Skeleton;
  readonly state: DamageState;
  private wire: string | null = null;
  private readonly bones: THREE.Bone[];
  constructor(readonly layout: DamageLayout) {
    this.state = new DamageState(layout);
    this.bones = Array.from({ length: layout.count }, () => {
      const bone = new THREE.Bone(); bone.matrixAutoUpdate = false; bone.matrixWorldAutoUpdate = false; return bone;
    });
    this.skeleton = new THREE.Skeleton(this.bones, this.bones.map(() => new THREE.Matrix4()));
  }
  /** Binds a body or glazing mesh that shares this cage (its geometry skinned with `skinToCage`). */
  bind(mesh: THREE.SkinnedMesh) {
    mesh.bindMode = THREE.DetachedBindMode;
    mesh.bind(this.skeleton, IDENTITY);
    // The cage only pulls the body inwards by centimetres: the undeformed bounds stay valid for culling, and the CPU
    // skinning pass r186 would otherwise run on the first culling test is never needed.
    mesh.geometry.computeBoundingBox(); mesh.geometry.computeBoundingSphere();
    mesh.boundingBox = mesh.geometry.boundingBox!.clone();
    mesh.boundingSphere = mesh.geometry.boundingSphere!.clone();
    mesh.boundingSphere.radius += .1;
  }
  /** Shows a wire damage state (vehicle/damage.ts encodeDamage); null or undefined is an undamaged body. */
  show(wire: string | null | undefined) {
    if ((wire ?? null) === this.wire) return;
    this.wire = wire ?? null;
    if (!wire || !decodeDamage(wire, this.state)) this.state.clear();
    this.pose();
  }
  /** The cage offset at a body point (fleet frame), e.g. a wheel hub pushed back with its crushed arch. */
  offsetAt(x: number, y: number, z: number, out: THREE.Vector3): THREE.Vector3 {
    const index = [0, 0, 0, 0], weight = [0, 0, 0, 0], offsets = this.state.offsets;
    cageWeights(this.layout, x, y, z, index, weight);
    out.set(0, 0, 0);
    for (let k = 0; k < 4; k++) out.add(new THREE.Vector3(offsets[index[k] * 3], offsets[index[k] * 3 + 1], offsets[index[k] * 3 + 2]).multiplyScalar(weight[k]));
    return out;
  }
  /** Moves the four authored hubs (actors/fleet/wheels.ts, after applyFleetWheels set their suspension height) with
   *  the cage at their pivots. Visual only: the physics wheels keep their mounts (handling effects come later). */
  placeWheels(actor: THREE.Object3D) {
    if (!this.state.damaged) return;
    for (let i = 0; i < 4; i++) {
      const hub = actor.getObjectByName(`fleet-wheel-${i}`), pivot = hub?.userData.pivot as { x: number; y: number; z: number } | undefined;
      if (!hub || !pivot) continue;
      if (this.hubRevision !== this.state.revision) this.offsetAt(pivot.x, pivot.y, pivot.z, this.hubs[i]);
      hub.position.x = pivot.x + this.hubs[i].x; hub.position.z = pivot.z + this.hubs[i].z;
      hub.position.y += this.hubs[i].y;
    }
    this.hubRevision = this.state.revision;
  }
  private hubs = [0, 1, 2, 3].map(() => new THREE.Vector3());
  private hubRevision = -1;
  /** Node transforms from the offsets: translation by the offset, rotation by the polar part of the displaced cage's
   *  local gradient (finite differences over the lattice neighbours), so dented panels turn their normals too. */
  pose() {
    const { stations, levels, laterals, nodes } = this.layout, offsets = this.state.offsets;
    const f = new THREE.Matrix3(), r = new THREE.Matrix4(), q = new THREE.Quaternion(), p = new THREE.Vector3();
    const e = new Array<number>(9);
    for (let s = 0; s < stations.length; s++) for (let h = 0; h < levels.length; h++) for (let l = 0; l < laterals.length; l++) {
      const i = nodeIndex(s, h, l);
      // Columns d(offset)/dx, d/dy, d/dz.
      const diff = (a: number, b: number, span: number, column: number) => {
        for (let k = 0; k < 3; k++) e[column * 3 + k] = (offsets[a * 3 + k] - offsets[b * 3 + k]) / span;
      };
      const l0 = Math.max(0, l - 1), l1 = Math.min(laterals.length - 1, l + 1);
      diff(nodeIndex(s, h, l1), nodeIndex(s, h, l0), laterals[l1] - laterals[l0], 0);
      diff(nodeIndex(s, 1, l), nodeIndex(s, 0, l), levels[1] - levels[0], 1);
      const s0 = Math.max(0, s - 1), s1 = Math.min(stations.length - 1, s + 1);
      diff(nodeIndex(s1, h, l), nodeIndex(s0, h, l), stations[s1] - stations[s0], 2);
      // F = I + grad; three's Matrix3.set takes rows, e holds columns.
      f.set(1 + e[0], e[3], e[6], e[1], 1 + e[4], e[7], e[2], e[5], 1 + e[8]);
      polarRotation(f, q);
      const angle = 2 * Math.acos(Math.min(1, Math.abs(q.w)));
      const along = (stations[s] - stations[0]) / (stations[stations.length - 1] - stations[0]);
      const limit = along > .2 && along < .8 ? CABIN_TILT : MAX_TILT;
      if (angle > limit) q.slerp(new THREE.Quaternion(), 1 - limit / angle);
      const bone = this.bones[i];
      p.set(nodes[i * 3], nodes[i * 3 + 1], nodes[i * 3 + 2]);
      // M = T(p + u) R T(-p)
      r.makeRotationFromQuaternion(q);
      const rp = p.clone().applyMatrix4(r);
      r.setPosition(p.x + offsets[i * 3] - rp.x, p.y + offsets[i * 3 + 1] - rp.y, p.z + offsets[i * 3 + 2] - rp.z);
      bone.matrixWorld.copy(r);
    }
  }
}

/** The rotation of F's polar decomposition (Higham's iteration; F stays well conditioned at cage strains). */
function polarRotation(f: THREE.Matrix3, out: THREE.Quaternion) {
  const r = f.clone(), inv = new THREE.Matrix3();
  for (let k = 0; k < 8; k++) {
    inv.copy(r).invert().transpose();
    for (let j = 0; j < 9; j++) r.elements[j] = .5 * (r.elements[j] + inv.elements[j]);
  }
  out.setFromRotationMatrix(new THREE.Matrix4().setFromMatrix3(r));
}

const layouts = new Map<string, DamageLayout>();
/** The cage of a fleet model, shared by every car of that model; the worker builds the same one from the spawn spec. */
export function modelLayout(model: FleetModel): DamageLayout {
  let layout = layouts.get(model.id);
  if (!layout) { layout = damageLayout(fleetDamageBox(model)); layouts.set(model.id, layout); }
  return layout;
}

/** The deformed bounds of a skinned body in its own frame (CPU skinning of every vertex), for QA and measurements. */
export function deformedBounds(mesh: THREE.SkinnedMesh): THREE.Box3 {
  const box = new THREE.Box3(), v = new THREE.Vector3(), position = mesh.geometry.getAttribute('position');
  mesh.skeleton.update();
  for (let i = 0; i < position.count; i++) box.expandByPoint(mesh.applyBoneTransform(i, v.fromBufferAttribute(position, i)));
  return box;
}
