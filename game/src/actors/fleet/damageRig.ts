import * as THREE from 'three/webgpu';
import type Node from 'three/src/nodes/core/Node.js';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { attribute, cross, int, ivec2, normalize, normalLocal, positionLocal, positionPrevious, textureLoad, uniform, vec3 } from 'three/tsl';
import { DAMAGE_NODES, DamageState, damageLayout, decodeDamage, nodeIndex, type DamageLayout } from '../../vehicle/damage';
import { fleetDamageBox, type FleetModel } from './catalog';

// Body damage on the GPU (vehicle/README.md "Body damage"). A damage-capable car body (the starter car, a driven or a
// remote car) carries `skinIndex`/`skinWeight` to the 54-node deformation cage of vehicle/damage.ts, and its material
// (damageMaterial) blends the node transforms in the vertex stage: rotation and translation per node, read from one
// shared pool texture by the car's slot (a per-object uniform, like the fleet paint). The vertex buffers never change,
// every car shares one program per pass (no skinning: r186 names each skeleton's bone buffer apart, which would build
// programs per car), and a wrecked car draws like a fresh one. The pool uploads only when a car's damage changes.

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

/** Damaged cars drawn at once with their own shape; slot 0 is the undamaged identity every fresh car reads. A car
 *  beyond the pool takes the slot used longest ago (that car draws fresh until it takes one back). */
export const DAMAGE_SLOTS = 32;
const ROW = DAMAGE_NODES * 2;
const poolData = new Uint16Array(ROW * DAMAGE_SLOTS * 4);
const ONE = THREE.DataUtils.toHalfFloat(1);
for (let i = 0; i < ROW * DAMAGE_SLOTS; i += 2) poolData[i * 4 + 3] = ONE;
/** Per slot row, per node: the rotation quaternion, then the translation (xyz), in half floats. */
const pool = new THREE.DataTexture(poolData, ROW, DAMAGE_SLOTS, THREE.RGBAFormat, THREE.HalfFloatType);
pool.name = 'fleet-damage-pool';
pool.minFilter = pool.magFilter = THREE.NearestFilter;
pool.generateMipmaps = false;
pool.colorSpace = THREE.NoColorSpace;
pool.needsUpdate = true;
const owners: (DamageRig | null)[] = new Array(DAMAGE_SLOTS).fill(null);
/** Each damage-capable mesh's rig (not userData: Object3D.copy clones that as JSON). */
const rigs = new WeakMap<THREE.Object3D, DamageRig>();
export const rigOf = (object: THREE.Object3D) => rigs.get(object);
const usedAt = new Float64Array(DAMAGE_SLOTS);
let clock = 0;

/** Fleet materials for damage-capable cars: the shared fleet look plus the cage deformation (see the file comment). */
export function deformByCage<M extends THREE.NodeMaterial>(material: M): M {
  // Vertices move before the model matrix: velocity and the normal prepass keep this program (rigidVelocity.ts).
  material.userData.vertexProgram = true;
  const setup = material.setupPosition.bind(material);
  material.setupPosition = (builder: NodeBuilder) => {
    const index = attribute('skinIndex', 'uvec4'), weight = attribute('skinWeight', 'vec4');
    const slot = int(uniform(0).onObjectUpdate(({ object }) => (object && rigs.get(object))?.slot ?? 0));
    const position = attribute('position', 'vec3'), normal = attribute('normal', 'vec3');
    const turn = (q: Node<'vec4'>, v: Node<'vec3'>) => v.add(cross(q.xyz, cross(q.xyz, v).add(v.mul(q.w))).mul(2));
    let p: Node<'vec3'> = vec3(0), n: Node<'vec3'> = vec3(0);
    for (const k of ['x', 'y', 'z', 'w'] as const) {
      const column = int(index[k]).mul(2);
      const q = textureLoad(pool, ivec2(column, slot)) as unknown as Node<'vec4'>;
      const t = (textureLoad(pool, ivec2(column.add(1), slot)) as unknown as Node<'vec4'>).xyz;
      p = p.add(turn(q, position).add(t).mul(weight[k]));
      n = n.add(turn(q, normal).mul(weight[k]));
    }
    positionLocal.assign(p);
    normalLocal.assign(normalize(n));
    // The shape only changes in a crash: last frame's position is this one (motion comes from the model matrix).
    if ((builder as NodeBuilder & { needsPreviousData(): boolean }).needsPreviousData()) positionPrevious.assign(p);
    return setup(builder);
  };
  return material;
}

/** One car's cage pose: the node transforms in the body's own (fleet model) frame, in the pool when damaged. Bind its
 *  meshes (body, glazing) with `bind`; `show` follows a wire state every frame (cheap when unchanged). */
export class DamageRig {
  readonly state: DamageState;
  /** The pool row this car's meshes read (0: the undamaged identity). */
  slot = 0;
  private wire: string | null = null;
  private readonly rotations = new Float32Array(DAMAGE_NODES * 4);
  private readonly translations = new Float32Array(DAMAGE_NODES * 3);
  constructor(readonly layout: DamageLayout) {
    this.state = new DamageState(layout);
    for (let i = 0; i < DAMAGE_NODES; i++) this.rotations[i * 4 + 3] = 1;
  }
  /** A body or glazing mesh of this car (its geometry skinned with `skinToCage`, its material `deformByCage`). */
  bind(mesh: THREE.Object3D) { rigs.set(mesh, this); }
  /** Shows a wire damage state (vehicle/damage.ts encodeDamage); null or undefined is an undamaged body. */
  show(wire: string | null | undefined) {
    if ((wire ?? null) !== this.wire) {
      this.wire = wire ?? null;
      if (!wire || !decodeDamage(wire, this.state)) this.state.clear();
      this.pose();
    }
    if (!this.state.damaged) { this.release(); return; }
    if (this.slot === 0 || owners[this.slot] !== this) this.acquire();
    usedAt[this.slot] = ++clock;
  }
  /** Gives the pool slot back (the car is gone or fresh again). */
  release() {
    if (this.slot && owners[this.slot] === this) owners[this.slot] = null;
    this.slot = 0;
  }
  private acquire() {
    let slot = owners.indexOf(null, 1);
    if (slot < 0) { slot = 1; for (let i = 2; i < DAMAGE_SLOTS; i++) if (usedAt[i] < usedAt[slot]) slot = i; }
    const previous = owners[slot];
    if (previous && previous !== this) previous.slot = 0;
    owners[slot] = this; this.slot = slot;
    this.upload();
  }
  private upload() {
    if (!this.slot) return;
    const base = this.slot * ROW * 4;
    for (let i = 0; i < DAMAGE_NODES; i++) {
      for (let k = 0; k < 4; k++) poolData[base + i * 8 + k] = THREE.DataUtils.toHalfFloat(this.rotations[i * 4 + k]);
      for (let k = 0; k < 3; k++) poolData[base + i * 8 + 4 + k] = THREE.DataUtils.toHalfFloat(this.translations[i * 3 + k]);
      poolData[base + i * 8 + 7] = 0;
    }
    pool.needsUpdate = true;
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
    const f = new THREE.Matrix3(), q = new THREE.Quaternion(), p = new THREE.Vector3(), identity = new THREE.Quaternion();
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
      if (angle > limit) q.slerp(identity, 1 - limit / angle);
      // x -> R (x - p) + p + u = R x + t
      p.set(nodes[i * 3], nodes[i * 3 + 1], nodes[i * 3 + 2]);
      const rp = p.clone().applyQuaternion(q);
      this.rotations.set([q.x, q.y, q.z, q.w], i * 4);
      this.translations.set([p.x + offsets[i * 3] - rp.x, p.y + offsets[i * 3 + 1] - rp.y, p.z + offsets[i * 3 + 2] - rp.z], i * 3);
    }
    this.upload();
  }
  /** A vertex of a skinned geometry as the shader draws it (CPU, for bounds and measurements). */
  deform(geometry: THREE.BufferGeometry, vertex: number, out: THREE.Vector3): THREE.Vector3 {
    const position = geometry.getAttribute('position'), index = geometry.getAttribute('skinIndex'), weight = geometry.getAttribute('skinWeight');
    const x = position.getX(vertex), y = position.getY(vertex), z = position.getZ(vertex), v = new THREE.Vector3(), q = new THREE.Quaternion();
    out.set(0, 0, 0);
    for (let k = 0; k < 4; k++) {
      const w = weight.getComponent(vertex, k);
      if (!w) continue;
      const n = index.getComponent(vertex, k), r = this.rotations, t = this.translations;
      q.set(r[n * 4], r[n * 4 + 1], r[n * 4 + 2], r[n * 4 + 3]);
      v.set(x, y, z).applyQuaternion(q).add(new THREE.Vector3(t[n * 3], t[n * 3 + 1], t[n * 3 + 2]));
      out.addScaledVector(v, w);
    }
    return out;
  }
  /** The deformed bounds of a skinned geometry in the body frame. */
  bounds(geometry: THREE.BufferGeometry): THREE.Box3 {
    const box = new THREE.Box3(), v = new THREE.Vector3(), n = geometry.getAttribute('position').count;
    for (let i = 0; i < n; i++) box.expandByPoint(this.deform(geometry, i, v));
    return box;
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

