import * as THREE from 'three/webgpu';
import { PART_COUNT, PART_STATE, PARTS, type DamageLayout, type DamageState, type PartName } from '../../vehicle/damage';

// Loose and detached body parts (vehicle/README.md "Loose and detached parts"). Each damage-capable body is split by
// region, once per geometry, into the parts of vehicle/damage.ts PARTS: the same rule for every fleet model, from its
// damage box. A `damagePart` vertex attribute (x: part index + 1, 0 the body) picks the part's rigid transform in the
// damage pool (damageRig.ts) before the cage blend: identity while attached, a swing about its remaining mount while
// loose, collapsed to a point once detached (debris.ts then draws a copy of it as a physics body).

/** Rest-frame bounds of a part, and the triangles (index triples into its geometry) it owns. */
export interface PartInfo { readonly name: PartName; readonly min: THREE.Vector3; readonly max: THREE.Vector3; readonly triangles: Uint32Array }
/** Per PARTS index: the part, or null when the model has none there (a body style without a boot lid, no mirrors). */
export type ModelParts = readonly (PartInfo | null)[];
const byGeometry = new WeakMap<THREE.BufferGeometry, ModelParts>();
/** The parts of a body geometry split by `splitParts` (shared by every clone of it). */
export const partsOf = (geometry: THREE.BufferGeometry): ModelParts | undefined => byGeometry.get(geometry);

/** Regions as fractions of the damage box: `f` along the length from the rear, `h` up the height, `a` out from the
 *  middle to the side. Bumpers: the lower 45 % of the end 17 % of the length. Bonnet: the up-facing panel ahead of
 *  the windscreen base (the front 30 %) inside the wings. Boot lid: the rear panel between the bumper line and the
 *  rear window (hatchback tailgates keep their glazed upper half on the body). Mirrors: small pieces sticking out past
 *  93 % of the half-width near the A-pillars (the box spans the mirrors). */
const REGION = { bumperH: .45, bumperF: .17, bonnetF: .7, bonnetUp: .3, panelA: .78, bootF: .16, bootH: [.42, .72],
  mirrorA: .93, mirrorF: [.5, .8], mirrorH: [.5, .85], mirrorSize: .45 } as const;
/** A connected piece goes to a part when this share of its area lies in that part's region and its bounds stay within
 *  the region and `MARGIN` (m): authored panels stay whole, and pieces reaching past (wheel-arch liners, the side
 *  shell, the scuttle) stay on the body. Only a fused shell spanning `SHELL` of the length is cut by region,
 *  triangle by triangle. */
const MAJORITY = .7, MARGIN = .1, SHELL = .8;
const PART_ID = Object.fromEntries(PARTS.map((name, i) => [name, i + 1])) as Record<PartName, number>;

function region(layout: DamageLayout, x: number, y: number, z: number, ny: number): number {
  const f = (z - layout.min[2]) / layout.length, h = (y - layout.min[1]) / layout.height;
  const a = Math.abs(x - (layout.min[0] + layout.max[0]) / 2) / (layout.width / 2);
  if (h < REGION.bumperH && f > 1 - REGION.bumperF) return PART_ID.frontBumper;
  if (h < REGION.bumperH && f < REGION.bumperF) return PART_ID.rearBumper;
  if (f > REGION.bonnetF && h >= REGION.bumperH - .03 && a < REGION.panelA && ny > REGION.bonnetUp) return PART_ID.bonnet;
  if (f < REGION.bootF && h >= REGION.bootH[0] && h < REGION.bootH[1] && a < REGION.panelA) return PART_ID.bootLid;
  return 0;
}
/** Whether a piece's bounds stay inside a part's region (plus MARGIN). */
function contained(layout: DamageLayout, part: number, box: THREE.Box3): boolean {
  const { min, max, length, width, height } = layout, cx = (min[0] + max[0]) / 2, side = Math.max(box.max.x - cx, cx - box.min.x);
  const below = (y: number, f: number) => y <= min[1] + f * height + MARGIN, above = (y: number, f: number) => y >= min[1] + f * height - MARGIN;
  if (part === PART_ID.frontBumper) return box.min.z >= max[2] - REGION.bumperF * length - MARGIN && below(box.max.y, REGION.bumperH);
  if (part === PART_ID.rearBumper) return box.max.z <= min[2] + REGION.bumperF * length + MARGIN && below(box.max.y, REGION.bumperH);
  if (part === PART_ID.bonnet) return box.min.z >= min[2] + REGION.bonnetF * length - MARGIN && above(box.min.y, REGION.bumperH - .03)
    && side <= REGION.panelA * width / 2 + MARGIN;
  if (part === PART_ID.bootLid) return box.max.z <= min[2] + REGION.bootF * length + MARGIN && above(box.min.y, REGION.bootH[0])
    && below(box.max.y, REGION.bootH[1]) && side <= REGION.panelA * width / 2 + MARGIN;
  return false;
}

/** Connected pieces of a geometry: by shared triangle corners, and optionally also by coincident positions. */
function pieces(position: THREE.BufferAttribute | THREE.InterleavedBufferAttribute, index: ArrayLike<number>, welded: boolean): Int32Array {
  const n = position.count, parent = new Int32Array(n).map((_, i) => i);
  const find = (i: number): number => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
  const union = (a: number, b: number) => { a = find(a); b = find(b); if (a !== b) parent[a] = b; };
  for (let t = 0; t < index.length; t += 3) { union(index[t], index[t + 1]); union(index[t], index[t + 2]); }
  if (welded) {
    const seen = new Map<string, number>();
    for (let i = 0; i < n; i++) {
      const key = `${Math.round(position.getX(i) * 1000)},${Math.round(position.getY(i) * 1000)},${Math.round(position.getZ(i) * 1000)}`;
      const other = seen.get(key);
      if (other === undefined) seen.set(key, i); else union(i, other);
    }
  }
  for (let i = 0; i < n; i++) parent[i] = find(i);
  return parent;
}

/** Splits a body geometry into its parts (once per geometry): classifies triangles by region, keeps connected pieces
 *  whole, duplicates vertices shared across a part boundary (so a swinging part never stretches a triangle to the
 *  body) and adds `damagePart`. Lamps stay on the body. Returns false when it was done before. */
export function splitParts(geometry: THREE.BufferGeometry, layout: DamageLayout): boolean {
  if (geometry.getAttribute('damagePart')) return false;
  const position = geometry.getAttribute('position'), normal = geometry.getAttribute('normal'), surface = geometry.getAttribute('fleetSurface');
  const n = position.count;
  let index = geometry.index?.array as ArrayLike<number> | undefined;
  if (!index) index = Uint32Array.from({ length: n }, (_, i) => i);
  const triangles = index.length / 3;
  const smooth = pieces(position, index, false), welded = smooth;
  // Per triangle: its region and area; per piece (triangle-connected: coincident positions often weld a mirror or a
  // bumper to the panel beside it): area by region and bounds.
  const own = new Uint8Array(triangles), area = new Float32Array(triangles);
  const votes = new Map<number, Float64Array>(), bounds = new Map<number, THREE.Box3>(), smoothBounds = new Map<number, THREE.Box3>();
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), m = new THREE.Vector3();
  const lampOf = (t: number) => !!surface && surface.getW(index[t * 3]) >= .5;
  for (let t = 0; t < triangles; t++) {
    const i0 = index[t * 3], i1 = index[t * 3 + 1], i2 = index[t * 3 + 2];
    a.fromBufferAttribute(position, i0); b.fromBufferAttribute(position, i1); c.fromBufferAttribute(position, i2);
    m.copy(a).add(b).add(c).divideScalar(3);
    const ny = (normal.getY(i0) + normal.getY(i1) + normal.getY(i2)) / 3;
    own[t] = lampOf(t) ? 0 : region(layout, m.x, m.y, m.z, ny);
    area[t] = b.clone().sub(a).cross(c.clone().sub(a)).length() / 2 + 1e-9;
    const r = welded[i0];
    let v = votes.get(r);
    if (!v) { v = new Float64Array(PART_COUNT + 1); votes.set(r, v); bounds.set(r, new THREE.Box3()); }
    v[own[t]] += area[t];
    bounds.get(r)!.expandByPoint(a).expandByPoint(b).expandByPoint(c);
    let sb = smoothBounds.get(smooth[i0]);
    if (!sb) { sb = new THREE.Box3(); smoothBounds.set(smooth[i0], sb); }
    sb.expandByPoint(a).expandByPoint(b).expandByPoint(c);
  }
  // Welded pieces: whole to the majority part when contained in its region, cut by region when a whole-car shell,
  // else the body.
  const decided = new Map<number, number>();
  for (const [r, v] of votes) {
    let total = 0, best = 0;
    for (let k = 0; k < v.length; k++) { total += v[k]; if (v[k] > v[best]) best = k; }
    const box = bounds.get(r)!;
    if (best && v[best] >= MAJORITY * total && contained(layout, best, box)) decided.set(r, best);
    else if (box.max.z - box.min.z < SHELL * layout.length) decided.set(r, 0);
  }
  // Mirrors: smooth pieces sticking out at the A-pillars.
  const mirror = new Map<number, number>(), centreX = (layout.min[0] + layout.max[0]) / 2;
  for (const [r, box] of smoothBounds) {
    const mid = box.getCenter(m), f = (mid.z - layout.min[2]) / layout.length, h = (mid.y - layout.min[1]) / layout.height;
    const out = Math.max(box.max.x - centreX, centreX - box.min.x) / (layout.width / 2);
    if (box.getSize(a).length() < REGION.mirrorSize && out > REGION.mirrorA && f > REGION.mirrorF[0] && f < REGION.mirrorF[1]
      && h > REGION.mirrorH[0] && h < REGION.mirrorH[1]) mirror.set(r, mid.x > centreX ? PART_ID.mirrorLeft : PART_ID.mirrorRight);
  }
  for (let t = 0; t < triangles; t++) {
    const m = mirror.get(smooth[index[t * 3]]), d = decided.get(welded[index[t * 3]]);
    if (m !== undefined) own[t] = m;
    else if (lampOf(t)) own[t] = 0;
    else if (d !== undefined) own[t] = d;
  }
  // Vertices whose triangles fall in different parts are duplicated per extra part.
  const vertexPart = new Int16Array(n).fill(-1), copies = new Map<number, number>(), extra: number[] = [];
  const out = new Uint32Array(index.length);
  for (let t = 0; t < triangles; t++) for (let k = 0; k < 3; k++) {
    const v = index[t * 3 + k], p = own[t];
    if (vertexPart[v] < 0 || vertexPart[v] === p) { vertexPart[v] = p; out[t * 3 + k] = v; continue; }
    const key = v * 8 + p;
    let copy = copies.get(key);
    if (copy === undefined) { copy = n + extra.length; copies.set(key, copy); extra.push(v); }
    out[t * 3 + k] = copy;
  }
  const total = n + extra.length, parts = new Uint8Array(total * 4);
  for (let v = 0; v < n; v++) parts[v * 4] = Math.max(0, vertexPart[v]);
  for (const [key, copy] of copies) parts[copy * 4] = key % 8;
  if (extra.length) {
    for (const name of Object.keys(geometry.attributes)) {
      const attribute = geometry.getAttribute(name) as THREE.BufferAttribute;
      const size = attribute.itemSize, array = new (attribute.array.constructor as new (n: number) => THREE.TypedArray)(total * size);
      array.set(attribute.array as THREE.TypedArray);
      extra.forEach((v, j) => { for (let k = 0; k < size; k++) array[(n + j) * size + k] = attribute.array[v * size + k]; });
      geometry.setAttribute(name, new THREE.BufferAttribute(array, size, attribute.normalized));
    }
  }
  geometry.setIndex(new THREE.BufferAttribute(total > 65535 ? out : Uint16Array.from(out), 1));
  geometry.setAttribute('damagePart', new THREE.BufferAttribute(parts, 4));
  // Part bounds and triangles (for the hinges and the debris copies).
  const info: (PartInfo | null)[] = PARTS.map(() => null), lists = PARTS.map(() => [] as number[]);
  const finalPosition = geometry.getAttribute('position');
  for (let t = 0; t < triangles; t++) if (own[t]) lists[own[t] - 1].push(t);
  PARTS.forEach((name, i) => {
    if (!lists[i].length) return;
    const min = new THREE.Vector3(Infinity, Infinity, Infinity), max = new THREE.Vector3(-Infinity, -Infinity, -Infinity);
    for (const t of lists[i]) for (let k = 0; k < 3; k++) { a.fromBufferAttribute(finalPosition, out[t * 3 + k]); min.min(a); max.max(a); }
    info[i] = { name, min, max, triangles: Uint32Array.from(lists[i].flatMap(t => [out[t * 3], out[t * 3 + 1], out[t * 3 + 2]])) };
  });
  byGeometry.set(geometry, info);
  return true;
}

/** Glazing and other surfaces without parts: every vertex on the body (the shared damage program reads the attribute). */
export function noParts(geometry: THREE.BufferGeometry) {
  if (!geometry.getAttribute('damagePart')) geometry.setAttribute('damagePart', new THREE.BufferAttribute(new Uint8Array(geometry.getAttribute('position').count * 4), 4));
}

// ------------------------------------------------------------------ loose parts: a cheap visual swing
/** How a loose part hangs and swings: `axis` of its hinge in the fleet frame, the hinge at its bounds (`pivot`, as
 *  fractions of its box per axis; bumpers by their free end), the rest angle `rest` (rad; a bumper's free end drops to
 *  the ground), the extra opening per (m/s)² of forward speed (`lift`: the wind under a popped bonnet), the swing
 *  frequency (Hz) and damping ratio, which car-frame acceleration drives it (`drive`: axis and gain, rad per m/s²) and
 *  the angle limits. */
interface Hinge { axis: 'x' | 'z'; pivot: (side: number) => [number, number, number]; rest: (info: PartInfo) => number; lift: number; hz: number; zeta: number;
  drive: { axis: 0 | 1 | 2; gain: number }; limits: (info: PartInfo) => [number, number]; sign: (side: number) => number }
const ground = (info: PartInfo) => Math.asin(Math.min(.6, Math.max(.05, (info.min.y - .02) / Math.max(.3, info.max.x - info.min.x))));
const bumper: Hinge = { axis: 'z', pivot: side => [side > 0 ? 0 : 1, .75, .5], rest: info => .85 * ground(info), lift: 0, hz: 2.2, zeta: .15,
  drive: { axis: 1, gain: .02 }, limits: info => [0, ground(info)], sign: side => side > 0 ? -1 : 1 };
const HINGES: Record<PartName, Hinge> = {
  frontBumper: bumper, rearBumper: bumper,
  bonnet: { axis: 'x', pivot: () => [.5, 1, 0], rest: () => .07, lift: .0016, hz: 1.6, zeta: .2, drive: { axis: 1, gain: -.02 }, limits: () => [0, 1.05], sign: () => -1 },
  bootLid: { axis: 'x', pivot: () => [.5, 1, 1], rest: () => .12, lift: .0004, hz: 1.4, zeta: .12, drive: { axis: 2, gain: .03 }, limits: () => [0, .9], sign: () => 1 },
  mirrorLeft: { axis: 'z', pivot: () => [0, .5, .5], rest: () => .9, lift: 0, hz: 2.5, zeta: .1, drive: { axis: 0, gain: .04 }, limits: () => [.3, 1.5], sign: () => -1 },
  mirrorRight: { axis: 'z', pivot: () => [1, .5, .5], rest: () => .9, lift: 0, hz: 2.5, zeta: .1, drive: { axis: 0, gain: -.04 }, limits: () => [.3, 1.5], sign: () => 1 },
};

/** One car's part transforms: per part a rotation about its hinge (quaternion) and a translation and scale (scale 0
 *  collapses a detached part), written by `pose` into the pool row (damageRig.ts). `moving` while a loose part swings. */
export class PartPoses {
  readonly rotations = new Float32Array(PART_COUNT * 4);
  readonly translations = new Float32Array(PART_COUNT * 4);
  private readonly angle = new Float32Array(PART_COUNT);
  private readonly rate = new Float32Array(PART_COUNT);
  private readonly q = new THREE.Quaternion();
  private readonly p = new THREE.Vector3();
  constructor() { this.reset(); }
  reset() {
    this.rotations.fill(0); this.translations.fill(0); this.angle.fill(-1); this.rate.fill(0);
    for (let i = 0; i < PART_COUNT; i++) { this.rotations[i * 4 + 3] = 1; this.translations[i * 4 + 3] = 1; }
  }
  /** Steps the loose parts' swing by `dt` (s) under the car-frame acceleration `accel` (m/s², fleet frame, gravity not
   *  included) at forward speed `speed` (m/s) and writes every part's transform; true when any part is loose. */
  update(parts: ModelParts | undefined, state: DamageState, dt: number, accel: readonly number[], speed: number): boolean {
    let loose = false;
    for (let i = 0; i < PART_COUNT; i++) {
      const info = parts?.[i], s = state.parts[i];
      const r = i * 4;
      if (!info || s === PART_STATE.attached) { this.set(r, 0, 0, 0, 1, 0, 0, 0, 1); this.angle[i] = -1; continue; }
      if (s === PART_STATE.detached) {
        // Collapsed onto its centre: no triangle left to draw.
        this.p.copy(info.min).add(info.max).multiplyScalar(.5);
        this.set(r, 0, 0, 0, 1, this.p.x, this.p.y, this.p.z, 0);
        continue;
      }
      loose = true;
      const hinge = HINGES[info.name], side = state.partSide[i] || 1, [lo, hi] = hinge.limits(info);
      // A part that just worked loose starts closed and falls to its rest angle.
      if (this.angle[i] < 0) { this.angle[i] = 0; this.rate[i] = 0; }
      const target = Math.min(hi, hinge.rest(info) + hinge.lift * speed * Math.max(0, speed)), w = 2 * Math.PI * hinge.hz;
      const steps = Math.max(1, Math.ceil(dt / (1 / 120))), h = Math.min(dt, .1) / steps;
      for (let k = 0; k < steps; k++) {
        this.rate[i] += h * (-w * w * (this.angle[i] - target) - 2 * hinge.zeta * w * this.rate[i] + hinge.drive.gain * w * w * accel[hinge.drive.axis]);
        this.angle[i] += h * this.rate[i];
        if (this.angle[i] < lo) { this.angle[i] = lo; this.rate[i] *= -.3; }
        if (this.angle[i] > hi) { this.angle[i] = hi; this.rate[i] *= -.3; }
      }
      const f = hinge.pivot(side);
      this.p.set(info.min.x + f[0] * (info.max.x - info.min.x), info.min.y + f[1] * (info.max.y - info.min.y), info.min.z + f[2] * (info.max.z - info.min.z));
      const angle = hinge.sign(side) * this.angle[i];
      this.q.setFromAxisAngle(hinge.axis === 'x' ? X : Z, angle);
      // x -> q (x - p) + p
      const t = this.p.clone().applyQuaternion(this.q);
      this.set(r, this.q.x, this.q.y, this.q.z, this.q.w, this.p.x - t.x, this.p.y - t.y, this.p.z - t.z, 1);
    }
    return loose;
  }
  private set(r: number, qx: number, qy: number, qz: number, qw: number, tx: number, ty: number, tz: number, scale: number) {
    this.rotations[r] = qx; this.rotations[r + 1] = qy; this.rotations[r + 2] = qz; this.rotations[r + 3] = qw;
    this.translations[r] = tx; this.translations[r + 1] = ty; this.translations[r + 2] = tz; this.translations[r + 3] = scale;
  }
}
const X = new THREE.Vector3(1, 0, 0), Z = new THREE.Vector3(0, 0, 1);
