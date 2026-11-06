import * as THREE from 'three/webgpu';
import type Node from 'three/src/nodes/core/Node.js';
import type NodeBuilder from 'three/src/nodes/core/NodeBuilder.js';
import { attribute, cross, float, int, ivec2, normalize, normalLocal, positionLocal, positionPrevious, step, textureLoad, uniform, vec3, vertexStage } from 'three/tsl';
import { cageOffset, cageWeights, DAMAGE_NODES, DamageState, damageLayout, decodeDamage, GLASS_STAGE, nodeIndex, PART_COUNT, PART_STATE, WINDOW_COUNT, WINDOWS, type DamageLayout } from '../../vehicle/damage';
import { fleetDamageBox, type FleetModel } from './catalog';
import { PartPoses, partsOf, splitParts, splitWindows, windowsOf, type ModelParts, type PartInfo } from './damageParts';

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


/** Adds `skinIndex`/`skinWeight` for the cage to a fleet body or glazing geometry (once per geometry), and the
 *  body's loose parts (`damagePart`, damageParts.ts; glazing has none). */
export function skinToCage(geometry: THREE.BufferGeometry, layout: DamageLayout, kind: 'body' | 'glass' = 'body') {
  for (const _ of skinSteps(geometry, layout, kind)) { /* run every phase now */ }
}

/** The same in phases of a few milliseconds each (a 27k-vertex body takes about 20 ms in all), yielding to the next
 *  frame in between: the fleet's actor warm-up skins every resident model while the game runs. */
export async function skinToCageAsync(geometry: THREE.BufferGeometry, layout: DamageLayout, kind: 'body' | 'glass' = 'body') {
  for (const _ of skinSteps(geometry, layout, kind)) await new Promise(resolve => setTimeout(resolve, 0));
}

function* skinSteps(geometry: THREE.BufferGeometry, layout: DamageLayout, kind: 'body' | 'glass'): Generator<void> {
  if (geometry.getAttribute('skinIndex')) return;
  if (kind === 'body') { splitParts(geometry, layout); yield; } else splitWindows(geometry, layout);
  const attribute = geometry.getAttribute('position'), n = attribute.count;
  // Fleet geometry is flat float32 (authored.ts flattenFleetAsset); read its array directly.
  const flat = attribute instanceof THREE.BufferAttribute && attribute.array instanceof Float32Array && !attribute.normalized && attribute.itemSize === 3;
  const xyz = flat ? attribute.array as Float32Array : Float32Array.from({ length: n * 3 }, (_, k) => attribute.getComponent(Math.floor(k / 3), k % 3));
  const position = { getX: (i: number) => xyz[i * 3], getY: (i: number) => xyz[i * 3 + 1], getZ: (i: number) => xyz[i * 3 + 2] };
  // Connected parts: shared triangle corners, and coincident positions (hard edges split the welded vertices).
  const parent = new Int32Array(n).map((_, i) => i);
  const find = (i: number): number => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
  const union = (a: number, b: number) => { a = find(a); b = find(b); if (a !== b) parent[a] = b; };
  const index = geometry.index;
  if (index) { const ix = index.array; for (let i = 0; i < index.count; i += 3) { union(ix[i], ix[i + 1]); union(ix[i], ix[i + 2]); } }
  else for (let i = 0; i < n; i += 3) { union(i, i + 1); union(i, i + 2); }
  yield;
  // Millimetre position keys (±10 m per axis fits 53 bits).
  const seen = new Map<number, number>();
  for (let i = 0; i < n; i++) {
    const key = ((Math.round(position.getX(i) * 1000) + 10000) * 20001 + Math.round(position.getY(i) * 1000) + 10000) * 20001
      + Math.round(position.getZ(i) * 1000) + 10000;
    const other = seen.get(key);
    if (other === undefined) seen.set(key, i); else union(i, other);
  }
  yield;
  // Per part root: bounds and centroid sums (typed arrays, no per-vertex allocation).
  const low = new Float32Array(n * 3).fill(Infinity), high = new Float32Array(n * 3).fill(-Infinity), sum = new Float64Array(n * 3), count = new Uint32Array(n);
  const root = new Int32Array(n);
  for (let i = 0; i < n; i++) {
    const r = root[i] = find(i), x = position.getX(i), y = position.getY(i), z = position.getZ(i);
    low[r * 3] = Math.min(low[r * 3], x); low[r * 3 + 1] = Math.min(low[r * 3 + 1], y); low[r * 3 + 2] = Math.min(low[r * 3 + 2], z);
    high[r * 3] = Math.max(high[r * 3], x); high[r * 3 + 1] = Math.max(high[r * 3 + 1], y); high[r * 3 + 2] = Math.max(high[r * 3 + 2], z);
    sum[r * 3] += x; sum[r * 3 + 1] += y; sum[r * 3 + 2] += z; count[r]++;
  }
  const rigid = new Map<number, { index: number[]; weight: number[] }>();
  for (let r = 0; r < n; r++) {
    if (!count[r] || Math.hypot(high[r * 3] - low[r * 3], high[r * 3 + 1] - low[r * 3 + 1], high[r * 3 + 2] - low[r * 3 + 2]) >= RIGID_PART_M) continue;
    const weights = { index: [0, 0, 0, 0], weight: [0, 0, 0, 0] };
    cageWeights(layout, sum[r * 3] / count[r], sum[r * 3 + 1] / count[r], sum[r * 3 + 2] / count[r], weights.index, weights.weight);
    rigid.set(r, weights);
  }
  yield;
  const skinIndex = new Uint8Array(n * 4), skinWeight = new Uint8Array(n * 4), w = new Float32Array(4);
  for (let i = 0; i < n; i++) {
    const part = rigid.get(root[i]);
    if (part) { skinIndex.set(part.index, i * 4); w.set(part.weight); }
    else cageWeights(layout, position.getX(i), position.getY(i), position.getZ(i), skinIndex, w, i * 4);
    // Unsigned bytes that sum to exactly 255: the rounding remainder goes to the largest weight.
    let total = 0, largest = 0;
    for (let k = 0; k < 4; k++) { skinWeight[i * 4 + k] = Math.round(w[k] * 255); total += skinWeight[i * 4 + k]; if (w[k] > w[largest]) largest = k; }
    skinWeight[i * 4 + largest] += 255 - total;
  }
  geometry.setAttribute('skinIndex', new THREE.BufferAttribute(skinIndex, 4));
  geometry.setAttribute('skinWeight', new THREE.BufferAttribute(skinWeight, 4, true));
}

/** Damaged cars drawn at once with their own shape; slot 0 is the undamaged identity every fresh car reads. A car
 *  beyond the pool takes the slot used longest ago (that car draws fresh until it takes one back). */
export const DAMAGE_SLOTS = 32;
/** Texels per slot row: two per cage node, then two per part (0 the body, always identity; damageParts.ts), then
 *  one per window (0 none; x the glass stage). */
const CAGE_TEXELS = DAMAGE_NODES * 2, PART_TEXELS = (PART_COUNT + 1) * 2, WINDOW_BASE = CAGE_TEXELS + PART_TEXELS;
const ROW = WINDOW_BASE + WINDOW_COUNT + 1;
const poolData = new Uint16Array(ROW * DAMAGE_SLOTS * 4);
const ONE = THREE.DataUtils.toHalfFloat(1);
// Identity everywhere: quaternion w of every even texel, and the scale (w) of every part's translation texel.
for (let i = 0; i < ROW * DAMAGE_SLOTS; i += 2) poolData[i * 4 + 3] = ONE;
for (let slot = 0; slot < DAMAGE_SLOTS; slot++) for (let k = 0; k <= PART_COUNT; k++) poolData[(slot * ROW + CAGE_TEXELS + k * 2 + 1) * 4 + 3] = ONE;
/** Per slot row, per node: the rotation quaternion, then the translation (xyz), in half floats; then per part its
 *  rotation and its translation and scale. */
const pool = new THREE.DataTexture(poolData, ROW, DAMAGE_SLOTS, THREE.RGBAFormat, THREE.HalfFloatType);
pool.name = 'fleet-damage-pool';
pool.minFilter = pool.magFilter = THREE.NearestFilter;
pool.generateMipmaps = false;
pool.colorSpace = THREE.NoColorSpace;
pool.needsUpdate = true;
const owners: (DamageRig | null)[] = new Array(DAMAGE_SLOTS).fill(null);
const ZERO3: readonly number[] = [0, 0, 0];
/** Each damage-capable mesh's rig (not userData: Object3D.copy clones that as JSON). */
const rigs = new WeakMap<THREE.Object3D, DamageRig>();
export const rigOf = (object: THREE.Object3D) => rigs.get(object);
const usedAt = new Float64Array(DAMAGE_SLOTS);
let clock = 0;

/** The pool row of the drawn object's car (a per-object uniform). */
const slotNode = () => int(uniform(0).onObjectUpdate(({ object }) => (object && rigs.get(object))?.slot ?? 0));
/** The glass stage of the drawn vertex's window (vehicle/damage.ts GLASS_STAGE), for the glazing's fragment stage. */
export function windowStage(): Node<'float'> {
  const texel = textureLoad(pool, ivec2(int(attribute('damagePart', 'uvec4').y).add(WINDOW_BASE), slotNode())) as unknown as Node<'vec4'>;
  return vertexStage(texel.x) as unknown as Node<'float'>;
}

/** Fleet materials for damage-capable cars: the shared fleet look plus the cage deformation (see the file comment).
 *  `windows`: glazing, whose shattered windows collapse (the frame stays empty). */
export function deformByCage<M extends THREE.NodeMaterial>(material: M, windows = false): M {
  // Vertices move before the model matrix: velocity and the normal prepass keep this program (rigidVelocity.ts).
  material.userData.vertexProgram = true;
  const setup = material.setupPosition.bind(material);
  material.setupPosition = (builder: NodeBuilder) => {
    const index = attribute('skinIndex', 'uvec4'), weight = attribute('skinWeight', 'vec4');
    const slot = slotNode();
    const turn = (q: Node<'vec4'>, v: Node<'vec3'>) => v.add(cross(q.xyz, cross(q.xyz, v).add(v.mul(q.w))).mul(2));
    // The vertex's part first (a loose part swings about its hinge, a detached one collapses), then the cage blend.
    const part = int(attribute('damagePart', 'uvec4').x).mul(2).add(CAGE_TEXELS);
    const partTurn = textureLoad(pool, ivec2(part, slot)) as unknown as Node<'vec4'>;
    const partMove = textureLoad(pool, ivec2(part.add(1), slot)) as unknown as Node<'vec4'>;
    const position = turn(partTurn, attribute('position', 'vec3')).add(partMove.xyz);
    const normal = turn(partTurn, attribute('normal', 'vec3'));
    let p: Node<'vec3'> = vec3(0), n: Node<'vec3'> = vec3(0);
    for (const k of ['x', 'y', 'z', 'w'] as const) {
      const column = int(index[k]).mul(2);
      const q = textureLoad(pool, ivec2(column, slot)) as unknown as Node<'vec4'>;
      const t = (textureLoad(pool, ivec2(column.add(1), slot)) as unknown as Node<'vec4'>).xyz;
      p = p.add(turn(q, position).add(t).mul(weight[k]));
      n = n.add(turn(q, normal).mul(weight[k]));
    }
    // A detached part (scale 0) collapses onto the origin after the blend: zero-area triangles draw nothing; so does
    // a shattered window.
    let keep: Node<'float'> = partMove.w as unknown as Node<'float'>;
    if (windows) {
      const stage = (textureLoad(pool, ivec2(int(attribute('damagePart', 'uvec4').y).add(WINDOW_BASE), slot)) as unknown as Node<'vec4'>).x;
      keep = keep.mul(float(1).sub(step(GLASS_STAGE.shattered - .5, stage)));
    }
    p = p.mul(keep);
    positionLocal.assign(p);
    normalLocal.assign(normalize(n));
    // The shape only changes in a crash: last frame's position is this one (motion comes from the model matrix).
    if ((builder as NodeBuilder & { needsPreviousData(): boolean }).needsPreviousData()) positionPrevious.assign(p);
    return setup(builder);
  };
  return material;
}

/** A part of a car just came off (debris.ts copies it into a physics body). `velocity`: the car's, world frame (m/s). */
export interface DetachEvent { rig: DamageRig; body: THREE.Mesh; part: number; info: PartInfo; velocity: THREE.Vector3 }
let detachListener: ((event: DetachEvent) => void) | null = null;
/** A window of a car just shattered (glassShards.ts throws its shards): `glass` the glazing mesh, `centre` the window's
 *  crack origin (fleet frame). */
export interface ShatterEvent { glass: THREE.Mesh; window: number; centre: THREE.Vector3; velocity: THREE.Vector3 }
let shatterListener: ((event: ShatterEvent) => void) | null = null;
export const onWindowShattered = (listener: ((event: ShatterEvent) => void) | null) => { shatterListener = listener; };
/** The debris field (debris.ts) listens for parts coming off any drawn car, local or remote. */
export const onPartDetached = (listener: ((event: DetachEvent) => void) | null) => { detachListener = listener; };

/** One car's cage pose: the node transforms in the body's own (fleet model) frame, in the pool when damaged. Bind its
 *  meshes (body, glazing) with `bind`; `show` follows a wire state every frame (cheap when unchanged and no part is
 *  loose). */
export class DamageRig {
  readonly state: DamageState;
  /** The pool row this car's meshes read (0: the undamaged identity). */
  slot = 0;
  private wire: string | null = null;
  private shown = false;
  private readonly rotations = new Float32Array(DAMAGE_NODES * 4);
  private readonly translations = new Float32Array(DAMAGE_NODES * 3);
  private readonly parts = new PartPoses();
  private readonly meshes: THREE.Mesh[] = [];
  private readonly partStates = new Uint8Array(PART_COUNT);
  private readonly glassStages = new Uint8Array(WINDOW_COUNT);
  // The body's motion for the loose parts' swing: last world position and velocity, smoothed acceleration (world).
  private motion: { at: number; p: THREE.Vector3; v: THREE.Vector3; a: THREE.Vector3 } | null = null;
  constructor(readonly layout: DamageLayout) {
    this.state = new DamageState(layout);
    for (let i = 0; i < DAMAGE_NODES; i++) this.rotations[i * 4 + 3] = 1;
  }
  /** A body or glazing mesh of this car (its geometry skinned with `skinToCage`, its material `deformByCage`); the
   *  first bound is the body, whose parts can come loose. */
  bind(mesh: THREE.Object3D) { rigs.set(mesh, this); if ((mesh as THREE.Mesh).isMesh) this.meshes.push(mesh as THREE.Mesh); }
  /** The body's parts (damageParts.ts), from its geometry. */
  get modelParts(): ModelParts | undefined { return this.meshes[0] && partsOf(this.meshes[0].geometry); }
  /** Shows a wire damage state (vehicle/damage.ts encodeDamage); null or undefined is an undamaged body. `now` (ms)
   *  drives the loose parts' swing. A part that comes off after the first state shown is announced (onPartDetached);
   *  one already off when the car is first drawn (a remote car coming into view, a loaded save) is just gone. */
  show(wire: string | null | undefined, now = performance.now()) {
    if ((wire ?? null) !== this.wire || !this.shown) {
      this.wire = wire ?? null;
      if (!wire || !decodeDamage(wire, this.state)) this.state.clear();
      this.pose();
      for (let i = 0; i < PART_COUNT; i++) {
        const next = this.state.parts[i], info = this.modelParts?.[i];
        if (this.shown && next === PART_STATE.detached && this.partStates[i] !== PART_STATE.detached && info && this.meshes[0])
          detachListener?.({ rig: this, body: this.meshes[0], part: i, info, velocity: this.motion?.v.clone() ?? new THREE.Vector3() });
        this.partStates[i] = next;
      }
      const glass = this.meshes[1], windows = glass && windowsOf(glass.geometry);
      for (let i = 0; i < WINDOW_COUNT; i++) {
        const next = this.state.glass[i], window = windows?.find(w => w.window === WINDOWS[i]);
        if (this.shown && next === GLASS_STAGE.shattered && this.glassStages[i] !== GLASS_STAGE.shattered && window)
          shatterListener?.({ glass, window: i, centre: window.centre, velocity: this.motion?.v.clone() ?? new THREE.Vector3() });
        this.glassStages[i] = next;
      }
      this.shown = true;
      this.posePart(now, true);
    } else this.posePart(now, false);
    if (!this.state.damaged) { this.release(); return; }
    if (this.slot === 0 || owners[this.slot] !== this) this.acquire();
    usedAt[this.slot] = ++clock;
  }
  /** Follows the body's motion and steps the loose parts; uploads their transforms while any part is loose (and once
   *  after a state change). */
  private posePart(now: number, changed: boolean) {
    const body = this.meshes[0];
    let accel: readonly number[] = ZERO3, speed = 0;
    if (body) {
      const p = new THREE.Vector3().setFromMatrixPosition(body.matrixWorld), m = this.motion;
      if (!m || now - m.at > 250 || now <= m.at) this.motion = { at: now, p, v: new THREE.Vector3(), a: new THREE.Vector3() };
      else {
        const dt = (now - m.at) / 1000, v = p.clone().sub(m.p).divideScalar(dt);
        const a = v.clone().sub(m.v).divideScalar(dt).clampLength(0, 40);
        m.a.lerp(a, Math.min(1, dt / .06)); m.v.copy(v); m.p.copy(p); m.at = now;
      }
      // Into the body (fleet) frame: +Z forward, +Y up, +X the car's left.
      const q = new THREE.Quaternion().setFromRotationMatrix(body.matrixWorld).invert(), m2 = this.motion!;
      const local = m2.a.clone().applyQuaternion(q), velocity = m2.v.clone().applyQuaternion(q);
      accel = [local.x, local.y, local.z]; speed = velocity.z;
    }
    const dt = this.lastPartAt === null ? 0 : Math.min(.1, Math.max(0, (now - this.lastPartAt) / 1000));
    this.lastPartAt = now;
    const loose = this.state.damaged && this.parts.update(this.modelParts, this.state, dt, accel, speed);
    if (loose || changed) this.uploadParts();
  }
  private lastPartAt: number | null = null;
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
    this.upload(); this.uploadParts();
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
  private uploadParts() {
    if (!this.slot) return;
    const windows = (this.slot * ROW + WINDOW_BASE + 1) * 4;
    for (let i = 0; i < WINDOW_COUNT; i++) poolData[windows + i * 4] = THREE.DataUtils.toHalfFloat(this.state.glass[i]);
    // Part 0 (the body) keeps its identity texels; parts 1.. follow PartPoses.
    const base = (this.slot * ROW + CAGE_TEXELS + 2) * 4, r = this.parts.rotations, t = this.parts.translations;
    for (let i = 0; i < PART_COUNT; i++) for (let k = 0; k < 4; k++) {
      poolData[base + i * 8 + k] = THREE.DataUtils.toHalfFloat(r[i * 4 + k]);
      poolData[base + i * 8 + 4 + k] = THREE.DataUtils.toHalfFloat(t[i * 4 + k]);
    }
    pool.needsUpdate = true;
  }
  /** The cage offset at a body point (fleet frame), e.g. a wheel hub pushed back with its crushed arch. */
  offsetAt(x: number, y: number, z: number, out: THREE.Vector3): THREE.Vector3 {
    const o = cageOffset(this.state, x, y, z, [0, 0, 0]);
    return out.set(o[0], o[1], o[2]);
  }
  /** Moves the four authored hubs (actors/fleet/wheels.ts, after applyFleetWheels set their suspension height) along
   *  and across the car with the cage at their pivots, as the physics moves the wheel mounts (vehicle.ts); their
   *  height comes from the physics wheel state, which includes a damaged corner's sag. */
  placeWheels(actor: THREE.Object3D) {
    if (!this.state.damaged) return;
    for (let i = 0; i < 4; i++) {
      const hub = actor.getObjectByName(`fleet-wheel-${i}`), pivot = hub?.userData.pivot as { x: number; y: number; z: number } | undefined;
      if (!hub || !pivot) continue;
      if (this.hubRevision !== this.state.revision) this.offsetAt(pivot.x, pivot.y, pivot.z, this.hubs[i]);
      hub.position.x = pivot.x + this.hubs[i].x; hub.position.z = pivot.z + this.hubs[i].z;
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
    const v = new THREE.Vector3(position.getX(vertex), position.getY(vertex), position.getZ(vertex)), q = new THREE.Quaternion();
    const part = geometry.getAttribute('damagePart')?.getX(vertex) ?? 0;
    const k = (part - 1) * 4, scale = part ? this.parts.translations[k + 3] : 1;
    if (part) {
      const r = this.parts.rotations, t = this.parts.translations;
      v.applyQuaternion(q.set(r[k], r[k + 1], r[k + 2], r[k + 3])).add(new THREE.Vector3(t[k], t[k + 1], t[k + 2]));
    }
    const x = v.x, y = v.y, z = v.z;
    out.set(0, 0, 0);
    for (let k = 0; k < 4; k++) {
      const w = weight.getComponent(vertex, k);
      if (!w) continue;
      const n = index.getComponent(vertex, k), r = this.rotations, t = this.translations;
      q.set(r[n * 4], r[n * 4 + 1], r[n * 4 + 2], r[n * 4 + 3]);
      v.set(x, y, z).applyQuaternion(q).add(new THREE.Vector3(t[n * 3], t[n * 3 + 1], t[n * 3 + 2]));
      out.addScaledVector(v, w);
    }
    return out.multiplyScalar(scale);
  }
  /** The deformed bounds of a skinned geometry in the body frame, without its detached parts. */
  bounds(geometry: THREE.BufferGeometry): THREE.Box3 {
    const box = new THREE.Box3(), v = new THREE.Vector3(), n = geometry.getAttribute('position').count, part = geometry.getAttribute('damagePart');
    for (let i = 0; i < n; i++) {
      const p = part?.getX(i) ?? 0;
      if (p && this.state.parts[p - 1] === PART_STATE.detached) continue;
      box.expandByPoint(this.deform(geometry, i, v));
    }
    return box;
  }
  /** A part as drawn now (deformed by the cage and its swing), as a plain geometry in the body frame: position,
   *  normal, colour and surface (the debris copy of a detached part, before it collapses). */
  partGeometry(geometry: THREE.BufferGeometry, info: PartInfo, part: number): THREE.BufferGeometry {
    const saved = this.parts.translations[part * 4 + 3];
    this.parts.translations[part * 4 + 3] = 1;
    const triangles = info.triangles, out = new THREE.BufferGeometry(), v = new THREE.Vector3(), n = new THREE.Vector3();
    const positions = new Float32Array(triangles.length * 3), normals = new Float32Array(triangles.length * 3);
    const normal = geometry.getAttribute('normal'), colour = geometry.getAttribute('color'), surface = geometry.getAttribute('fleetSurface');
    const colours = new Float32Array(triangles.length * 3), surfaces = new Float32Array(triangles.length * 4);
    // The part's own transform without collapse; the normals turn with it (the cage's turn is small at a part).
    const r = this.parts.rotations, q = new THREE.Quaternion(r[part * 4], r[part * 4 + 1], r[part * 4 + 2], r[part * 4 + 3]);
    triangles.forEach((vertex, j) => {
      this.deform(geometry, vertex, v); positions.set([v.x, v.y, v.z], j * 3);
      n.fromBufferAttribute(normal, vertex).applyQuaternion(q); normals.set([n.x, n.y, n.z], j * 3);
      if (colour) colours.set([colour.getX(vertex), colour.getY(vertex), colour.getZ(vertex)], j * 3);
      if (surface) surfaces.set([surface.getX(vertex), surface.getY(vertex), surface.getZ(vertex), surface.getW(vertex)], j * 4);
    });
    this.parts.translations[part * 4 + 3] = saved;
    out.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    out.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
    out.setAttribute('color', new THREE.BufferAttribute(colours, 3));
    out.setAttribute('fleetSurface', new THREE.BufferAttribute(surfaces, 4));
    return out;
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

