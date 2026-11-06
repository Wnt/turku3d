// Car body damage: a deformation cage of 54 nodes per car, the crumple model and the compact synced state.
// Headless and shared: the physics worker owns each car's DamageState (vehicle/vehicle.ts) and the renderer skins the
// body to the same layout (actors/fleet/damageRig.ts). Everything here is in the fleet model frame: metres, +Z forward,
// +Y up, the tyres on Y = 0 (actors/fleet/catalog.ts). Calibration: ~/turku3d/research/wreckfest2/damage-study.md
// (Wreckfest 2 head-on sweeps, repeat hits and the per-vertex capture), docs in vehicle/README.md "Body damage".

/** The car's collision box in the fleet frame (registry `collision`), which every client and the worker share. */
export interface DamageBox { center: readonly [number, number, number]; half: readonly [number, number, number] }

/** Stations along the length, as fractions from the rear: dense at both crumple zones, one cabin station. */
const STATIONS = [0, .07, .14, .235, .5, .765, .86, .93, 1] as const;
/** Two levels (sill/bumper and belt/bonnet line) as fractions of the height, three laterals as fractions of the width. */
const LEVELS = [.3, .72] as const;
const LATERALS = [-.38, 0, .38] as const;
export const DAMAGE_NODES = STATIONS.length * LEVELS.length * LATERALS.length;

export interface DamageLayout {
  readonly count: number;
  /** Node positions, xyz per node: index (station * 2 + level) * 3 + lateral, stations from the rear. */
  readonly nodes: Float32Array;
  readonly stations: Float32Array; readonly levels: Float32Array; readonly laterals: Float32Array;
  readonly min: readonly [number, number, number]; readonly max: readonly [number, number, number];
  readonly length: number; readonly width: number; readonly height: number;
}

export const nodeIndex = (station: number, level: number, lateral: number) =>
  (station * LEVELS.length + level) * LATERALS.length + lateral;

/** The cage of a car from its box. The same rule serves every fleet model (body types may refine it later). */
export function damageLayout(box: DamageBox): DamageLayout {
  const [cx, cy, cz] = box.center, [hx, hy, hz] = box.half;
  const min = [cx - hx, cy - hy, cz - hz] as const, max = [cx + hx, cy + hy, cz + hz] as const;
  const length = 2 * hz, width = 2 * hx, height = 2 * hy;
  const stations = Float32Array.from(STATIONS, f => min[2] + f * length);
  const levels = Float32Array.from(LEVELS, f => min[1] + f * height);
  const laterals = Float32Array.from(LATERALS, f => cx + f * width);
  const nodes = new Float32Array(DAMAGE_NODES * 3);
  for (let s = 0; s < stations.length; s++) for (let h = 0; h < levels.length; h++) for (let l = 0; l < laterals.length; l++)
    nodes.set([laterals[l], levels[h], stations[s]], nodeIndex(s, h, l) * 3);
  return { count: DAMAGE_NODES, nodes, stations, levels, laterals, min, max, length, width, height };
}

// ------------------------------------------------------------------ crumple model
/** Energy-like hit severity is (dv km/h)² minus the threshold², counted per face. Fitted to the Wreckfest 2 table:
 *  single head-on hits 0 cm at 33 km/h, 20 at 52, 33 at 71, 42 at 81; six 61.5 km/h hits 28 → 46 cm; eight 42.5 km/h
 *  hits about one 70 km/h hit (damage-study.md "Head-on speed sweep", "Repeat hits"). */
export const CRUMPLE = {
  /** No structural crumple below this speed change (km/h). */
  thresholdKmh: 35,
  /** Saturation scale (km/h)² of D = Dmax (1 - exp(-E / E0)). */
  energyScale: 6000,
  /** Curve asymptote and hard stop, as fractions of the car length (Wreckfest 2: the hard stop at 46 cm of 4.42 m). */
  asymptote: .149, stop: .105,
  /** The soft bumper layer: a few centimetres from low speed on, on the end faces only. */
  bumperKmh: 15, bumperDepth: .03, bumperScale: 500,
} as const;

/** Faces of the box: the ends, the -X side (the car's right, seen from the driver's seat) and +X side (its left), the roof. */
type Face = 'front' | 'rear' | 'negX' | 'posX' | 'top';
/** Per face: hard stop as a fraction of the car dimension across it, energy scale factor (higher = stiffer),
 *  depth falloff: full depth up to `s0`, linear to `floor` at `s1`, then to 0 at `s2` (fractions of that dimension). */
const FACES: Record<Face, { stop: number; stiffness: number; s0: number; s1: number; s2: number; floor: number }> = {
  // The front 0.5 m moves as one, 20 cm falls to ~1 cm within the next 0.5 m, the cabin stays within 1.5 cm.
  front: { stop: CRUMPLE.stop, stiffness: 1, s0: .117, s1: .235, s2: .5, floor: .03 },
  rear: { stop: CRUMPLE.stop, stiffness: 1, s0: .117, s1: .235, s2: .5, floor: .03 },
  // Doors and pillars: less crush space than the ends, softer per unit of energy; the far side stays.
  negX: { stop: .15, stiffness: .6, s0: .06, s1: .5, s2: .9, floor: .08 },
  posX: { stop: .15, stiffness: .6, s0: .06, s1: .5, s2: .9, floor: .08 },
  // A very stiff roof.
  top: { stop: .12, stiffness: 2.5, s0: .08, s1: .55, s2: 1, floor: .05 },
};
const FACE_LIST: readonly Face[] = ['front', 'rear', 'negX', 'posX', 'top'];
/** Energy bins per face: front and rear by lateral (-X, centre, +X), sides by thirds of the length (rear, mid,
 *  front), one roof bin; then the bumper layer's bins for front and rear. */
const BIN_OFFSET: Record<Face, number> = { front: 0, rear: 3, negX: 6, posX: 9, top: 12 };
const BUMPER_OFFSET = { front: 13, rear: 16 } as const;
export const DAMAGE_BINS = 19;

/** A hit on the car. `dv`: the car's velocity change from the contact (m/s, fleet frame); the faces whose outward
 *  normal opposes it are crushed by their share (squared direction cosines, so the shares add up to the whole hit).
 *  `at`: the contact point (fleet frame, default each face's centre); `halfWidth`: the half-width of the contact
 *  footprint (m, default a wall wider than the car; a pole is ~0.15). */
export interface DamageHit { dv: readonly [number, number, number]; at?: readonly [number, number, number]; halfWidth?: number }

/** One car's damage. `offsets` (xyz per node, metres) is the shape the renderer draws and multiplayer replicates;
 *  `bins` the absorbed energy per face region that saturation reads; `energy` and `hits` keep counting after the shape
 *  saturates (hooks for handling, mechanical damage and loose parts). `revision` changes with every applied hit. */
export class DamageState {
  readonly offsets = new Float32Array(DAMAGE_NODES * 3);
  readonly bins = new Float32Array(DAMAGE_BINS);
  energy = 0; hits = 0; revision = 0;
  constructor(readonly layout: DamageLayout) {}
  get damaged() { return this.hits > 0; }
  clear() { this.offsets.fill(0); this.bins.fill(0); this.energy = 0; this.hits = 0; this.revision++; }
}

const smooth = (t: number) => { const x = Math.max(0, Math.min(1, t)); return x * x * (3 - 2 * x); };
/** Structural face depth (m) of a bin with absorbed energy `e`, capped at the face's hard stop. */
function faceDepth(face: Face, e: number, layout: DamageLayout): number {
  const across = face === 'front' || face === 'rear' ? layout.length : face === 'top' ? layout.height : layout.width;
  const stop = FACES[face].stop * across, asymptote = stop * CRUMPLE.asymptote / CRUMPLE.stop;
  return Math.min(stop, asymptote * (1 - Math.exp(-e / CRUMPLE.energyScale)));
}
const bumperDepth = (e: number) => CRUMPLE.bumperDepth * (1 - Math.exp(-e / CRUMPLE.bumperScale));
function falloff(face: Face, depth: number, across: number): number {
  const f = FACES[face], s = depth / across;
  if (s <= f.s0) return 1;
  if (s <= f.s1) return 1 - (1 - f.floor) * (s - f.s0) / (f.s1 - f.s0);
  return Math.max(0, f.floor * (1 - (s - f.s1) / (f.s2 - f.s1)));
}
/** A footprint narrower than the face dents most under it and pulls the panel around it a little. */
const lateralShare = (distance: number, halfWidth: number) => distance <= halfWidth ? 1 : 1 - .75 * smooth((distance - halfWidth) / .8);
/** Deterministic per node and face, so the same state always draws the same irregular crumple. */
function jitter(node: number, face: number, salt: number): number {
  let h = Math.imul(node + 1, 0x9e3779b1) ^ Math.imul(face + 7, 0x85ebca6b) ^ Math.imul(salt + 3, 0xc2b2ae35);
  h = Math.imul(h ^ h >>> 15, 0x2c1b3c6d); h ^= h >>> 13;
  return .6 + .8 * ((h >>> 0) / 4294967296);
}

/** Node offsets for the given bins: each face pushes its nodes inward by its depth times the depth falloff, the bonnet
 *  or boot lid buckles up in the crumple band, the sides there bulge out and the pushed end droops a little
 *  (Wreckfest 2: the front 0.5 m moved back 19-20 cm and 2-3 cm down, width and height grew 1-3 cm). */
function shape(layout: DamageLayout, bins: Float32Array, out: Float32Array) {
  out.fill(0);
  const { stations, levels, laterals, min, max, length, width, height } = layout;
  const last = stations.length - 1;
  for (let s = 0; s <= last; s++) for (let h = 0; h < levels.length; h++) for (let l = 0; l < laterals.length; l++) {
    const i = nodeIndex(s, h, l), o = i * 3, x = laterals[l], y = levels[h], z = stations[s];
    for (let f = 0; f < FACE_LIST.length; f++) {
      const face = FACE_LIST[f];
      if (face === 'front' || face === 'rear') {
        const front = face === 'front', depth = front ? max[2] - z : z - min[2];
        const D = faceDepth(face, bins[BIN_OFFSET[face] + l], layout);
        const end = front ? s === last : s === 0;
        const bumper = end ? bumperDepth(bins[BUMPER_OFFSET[face] + l]) : 0;
        if (D <= 0 && bumper <= 0) continue;
        const a = falloff(face, depth, length), sign = front ? -1 : 1;
        out[o + 2] += sign * (a * D + bumper);
        // Buckle and bulge peak inside the crumple band, zero at the end face and from the cabin on.
        const band = Math.sin(Math.PI * Math.min(1, depth / (FACES[face].s1 * length)));
        if (h === levels.length - 1) out[o + 1] += .25 * band * D * jitter(i, f, 0) * (l === 1 ? 1 : .6);
        else out[o + 1] -= .03 * band * D * jitter(i, f, 1);
        if (l !== 1) out[o] += Math.sign(x - layout.laterals[1]) * .07 * band * D * jitter(i, f, 2);
        out[o + 1] -= .1 * a * D;
      } else if (face === 'negX' || face === 'posX') {
        const neg = face === 'negX', depth = neg ? x - min[0] : max[0] - x;
        const t = (z - min[2]) / length * 3 - .5, k = Math.max(0, Math.min(1.999, t)), j = Math.floor(k), w = k - j;
        const e = bins[BIN_OFFSET[face] + j] * (1 - w) + bins[BIN_OFFSET[face] + j + 1] * w;
        const D = faceDepth(face, e, layout);
        if (D <= 0) continue;
        const a = falloff(face, depth, width);
        out[o] += (neg ? 1 : -1) * a * D;
        if (h === levels.length - 1) out[o + 1] -= .12 * a * D * jitter(i, f, 0);
        out[o + 2] += (jitter(i, f, 1) - 1) * .15 * a * D;
      } else {
        const D = faceDepth(face, bins[BIN_OFFSET.top], layout);
        if (D <= 0) continue;
        const a = falloff(face, max[1] - y, height);
        out[o + 1] -= a * D;
        if (l !== 1) out[o] += Math.sign(x - layout.laterals[1]) * .06 * a * D * jitter(i, f, 0);
      }
    }
  }
}

const KMH = 3.6;
/** Applies one hit: per face its energy beyond the threshold enters the bins under the footprint (less as a bin nears
 *  its stop: deformed structure is harder), and the node offsets move by the change of the shape. Returns the
 *  structural depth added at the most-hit face region (m), 0 when the hit was too light to deform anything. */
export function applyHit(state: DamageState, hit: DamageHit): number {
  const layout = state.layout, [dx, dy, dz] = hit.dv;
  const halfWidth = hit.halfWidth ?? Infinity;
  const before = new Float32Array(DAMAGE_NODES * 3);
  shape(layout, state.bins, before);
  let added = 0, total = 0, absorbed = false;
  // A face is crushed when the contact pushes the car away from it: the front by a push towards -Z, and so on.
  const pushes: Record<Face, number> = { front: Math.max(0, -dz), rear: Math.max(0, dz), negX: Math.max(0, dx),
    posX: Math.max(0, -dx), top: Math.max(0, -dy) };
  for (const face of FACE_LIST) {
    const kmh = pushes[face] * KMH;
    const e = Math.max(0, kmh * kmh - CRUMPLE.thresholdKmh ** 2) / FACES[face].stiffness;
    const eBumper = face === 'front' || face === 'rear' ? Math.max(0, kmh * kmh - CRUMPLE.bumperKmh ** 2) : 0;
    if (e <= 0 && eBumper <= 0) continue;
    total += e; absorbed = true;
    const across = face === 'front' || face === 'rear' ? layout.length : face === 'top' ? layout.height : layout.width;
    const stop = FACES[face].stop * across;
    const bins = face === 'top' ? [0] : [0, 1, 2];
    for (const b of bins) {
      // Bin centres: the laterals for the end faces, thirds of the length for the sides.
      let share = 1;
      if (face === 'front' || face === 'rear') share = lateralShare(Math.abs(layout.laterals[b] - (hit.at?.[0] ?? layout.laterals[1])), halfWidth);
      else if (face !== 'top') {
        const centre = layout.min[2] + (b + .5) / 3 * layout.length;
        share = lateralShare(Math.abs(centre - (hit.at?.[2] ?? (layout.min[2] + layout.max[2]) / 2)), halfWidth + layout.length / 6);
      }
      const index = BIN_OFFSET[face] + b, depth = faceDepth(face, state.bins[index], layout);
      state.bins[index] += e * share * Math.max(0, 1 - depth / stop);
      added = Math.max(added, faceDepth(face, state.bins[index], layout) - depth);
      if (face === 'front' || face === 'rear') state.bins[BUMPER_OFFSET[face] + b] += eBumper * share;
    }
  }
  if (!absorbed) return 0;
  const after = new Float32Array(DAMAGE_NODES * 3);
  shape(layout, state.bins, after);
  for (let i = 0; i < after.length; i++) state.offsets[i] = clampOffset(state.offsets[i] + after[i] - before[i]);
  state.energy += total; state.hits++; state.revision++;
  return added;
}

/** Predicted bounding-box length loss (m) of `hits` identical head-on wall hits at `kmh`: the end face's structural
 *  depth plus the bumper layer, as the renderer's deformed bounds measure it. For tests and calibration. */
export function headOnLoss(layout: DamageLayout, kmh: number, hits = 1): number {
  const state = new DamageState(layout);
  for (let i = 0; i < hits; i++) applyHit(state, { dv: [0, 0, -kmh / KMH] });
  return -Math.min(...[0, 1, 2].map(l => state.offsets[nodeIndex(layout.stations.length - 1, 0, l) * 3 + 2]));
}

// ------------------------------------------------------------------ compact state (worker → renderer, multiplayer, saves)
/** Offsets are quantized to 5 mm in int8 (±63.5 cm), bins to 8 (km/h)² in uint16. */
const OFFSET_UNIT = .005, BIN_UNIT = 8, WIRE_VERSION = 1;
const clampOffset = (v: number) => Math.max(-127 * OFFSET_UNIT, Math.min(127 * OFFSET_UNIT, v));
const HEADER = 8;
export const DAMAGE_WIRE_BYTES = HEADER + DAMAGE_BINS * 2 + DAMAGE_NODES * 3;

/** Wire form of a damage state: base64 of version, node count, hits, energy (64 (km/h)² units), bins, offsets. */
export function encodeDamage(state: DamageState): string {
  const bytes = new Uint8Array(DAMAGE_WIRE_BYTES), view = new DataView(bytes.buffer);
  view.setUint8(0, WIRE_VERSION); view.setUint8(1, DAMAGE_NODES); view.setUint16(2, Math.min(65535, state.hits), true);
  view.setUint32(4, Math.min(0xffffffff, Math.round(state.energy / 64)), true);
  for (let i = 0; i < DAMAGE_BINS; i++) view.setUint16(HEADER + i * 2, Math.min(65535, Math.round(state.bins[i] / BIN_UNIT)), true);
  const base = HEADER + DAMAGE_BINS * 2;
  for (let i = 0; i < DAMAGE_NODES * 3; i++) view.setInt8(base + i, Math.round(state.offsets[i] / OFFSET_UNIT));
  let text = '';
  for (const b of bytes) text += String.fromCharCode(b);
  return btoa(text);
}

/** Reads a wire string into `state` (its revision moves on); false and `state` unchanged for anything malformed. */
export function decodeDamage(wire: unknown, state: DamageState): boolean {
  if (typeof wire !== 'string' || wire.length > 4 * Math.ceil(DAMAGE_WIRE_BYTES / 3)) return false;
  let text: string;
  try { text = atob(wire); } catch { return false; }
  if (text.length !== DAMAGE_WIRE_BYTES) return false;
  const bytes = Uint8Array.from(text, c => c.charCodeAt(0)), view = new DataView(bytes.buffer);
  if (view.getUint8(0) !== WIRE_VERSION || view.getUint8(1) !== DAMAGE_NODES) return false;
  state.hits = view.getUint16(2, true); state.energy = view.getUint32(4, true) * 64;
  for (let i = 0; i < DAMAGE_BINS; i++) state.bins[i] = view.getUint16(HEADER + i * 2, true) * BIN_UNIT;
  const base = HEADER + DAMAGE_BINS * 2;
  for (let i = 0; i < DAMAGE_NODES * 3; i++) state.offsets[i] = view.getInt8(base + i) * OFFSET_UNIT;
  state.revision++;
  return true;
}

/** A valid wire string (servers and saves keep only these). */
export const validDamageWire = (wire: unknown) => decodeDamage(wire, new DamageState(damageLayout({ center: [0, 0, 0], half: [1, 1, 1] })));
