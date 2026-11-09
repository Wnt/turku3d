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

/** Up to four cage nodes and weights for a point: Kuhn tetrahedra of the station × level × lateral cell it lies in
 *  (clamped to the cage), so the weights reproduce any affine offset field exactly and stay continuous across cells. */
export function cageWeights(layout: DamageLayout, x: number, y: number, z: number, index: Uint8Array | number[], weight: Float32Array | number[], at = 0) {
  const { stations, levels, laterals } = layout;
  let s = 0;
  while (s < stations.length - 2 && z > stations[s + 1]) s++;
  let l = 0;
  while (l < laterals.length - 2 && x > laterals[l + 1]) l++;
  const tx = clamp01((x - laterals[l]) / (laterals[l + 1] - laterals[l])), ty = clamp01((y - levels[0]) / (levels[1] - levels[0]));
  const tz = clamp01((z - stations[s]) / (stations[s + 1] - stations[s]));
  // Walk from the cell's low corner along the axes in decreasing order of their coordinate (ties: x, y, z).
  let a = 0, ta = tx, b = 1, tb = ty, c = 2, tc = tz;
  if (tb > ta) { [a, ta, b, tb] = [b, tb, a, ta]; }
  if (tc > tb) { [b, tb, c, tc] = [c, tc, b, tb]; if (tb > ta) { [a, ta, b, tb] = [b, tb, a, ta]; } }
  let cl = l, ch = 0, cs = s;
  index[at] = nodeIndex(cs, ch, cl); weight[at] = 1 - ta;
  if (a === 0) cl++; else if (a === 1) ch++; else cs++;
  index[at + 1] = nodeIndex(cs, ch, cl); weight[at + 1] = ta - tb;
  if (b === 0) cl++; else if (b === 1) ch++; else cs++;
  index[at + 2] = nodeIndex(cs, ch, cl); weight[at + 2] = tb - tc;
  if (c === 0) cl++; else if (c === 1) ch++; else cs++;
  index[at + 3] = nodeIndex(cs, ch, cl); weight[at + 3] = tc;
}
const clamp01 = (v: number) => v < 0 ? 0 : v > 1 ? 1 : v;

/** The cage offset (m, fleet frame) at a body point of a damage state, e.g. a wheel hub pushed back with its crushed
 *  arch: the drawn body and the physics wheel mounts (vehicle.ts) both move by it. */
export function cageOffset(state: DamageState, x: number, y: number, z: number, out: [number, number, number]) {
  const index = [0, 0, 0, 0], weight = [0, 0, 0, 0], offsets = state.offsets;
  cageWeights(state.layout, x, y, z, index, weight);
  out[0] = out[1] = out[2] = 0;
  for (let k = 0; k < 4; k++) for (let a = 0; a < 3; a++) out[a] += offsets[index[k] * 3 + a] * weight[k];
  return out;
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

/** Mechanical systems a hit wears (vehicle/mechanics.ts reads their levels): the radiator, the engine, the steering
 *  and the four wheel corners in the physics wheel order (front left, front right, rear left, rear right). */
export const WEAR = { cooling: 0, engine: 1, steering: 2, corners: 3 } as const;
export const WEAR_SYSTEMS = 7;

/** Body parts that can work loose and come off, in wire order. The renderer finds each one in the model by region
 *  (actors/fleet/damageParts.ts) and draws it as part `index + 1` (0 is the body). */
export const PARTS = ['frontBumper', 'rearBumper', 'bonnet', 'bootLid', 'mirrorLeft', 'mirrorRight'] as const;
export type PartName = typeof PARTS[number];
export const PART_COUNT = PARTS.length;
/** Part states: on the car, hanging loose (swinging on its remaining mount), torn off (debris, gone for good). */
export const PART_STATE = { attached: 0, loose: 1, detached: 2 } as const;
/** A part's load per hit is (km/h of its face's push - `v0`)² times its footprint share, summed over hits; it hangs loose
 *  from `loose` and tears off from `off`. Wreckfest 2 detached nothing in single hits up to 84 km/h (Normal and
 *  Realistic) and its bumper beam hung after 4 x 61.5 km/h on Realistic (damage-study.md Part 2); tear-off is a design
 *  choice reachable with repeated hard hits. Bumpers: loose after 3 hits at 60 km/h or one at 75, off after 5 at 60 or
 *  one at 90. Bonnet and boot lid (their latch, then the hinges): loose after 4 at 60 or one at 81, off after 7 at 60
 *  or one at 99. Mirrors (side hits over the front two thirds, half of a roof hit): loose from one 27 km/h side
 *  impact, off from one at 38 km/h or a few lighter ones. */
export const PART_LOAD: Record<PartName, { v0: number; loose: number; off: number }> = {
  frontBumper: { v0: 36, loose: 1500, off: 2600 }, rearBumper: { v0: 36, loose: 1500, off: 2600 },
  bonnet: { v0: 36, loose: 2000, off: 4000 }, bootLid: { v0: 36, loose: 2000, off: 4000 },
  mirrorLeft: { v0: 15, loose: 150, off: 500 }, mirrorRight: { v0: 15, loose: 150, off: 500 },
};
/** Windows that crack and shatter, in wire order. The renderer finds each in the model's glazing by its facing
 *  (actors/fleet/damageParts.ts splitWindows) and draws it as window `index + 1` (0: lamp covers, never broken). */
export const WINDOWS = ['windscreen', 'rearWindow', 'frontLeft', 'rearLeft', 'frontRight', 'rearRight'] as const;
export type WindowName = typeof WINDOWS[number];
export const WINDOW_COUNT = WINDOWS.length;
/** Glass stages: intact, a crack star at the impact, crazed all over, shattered (the frame stays empty). */
export const GLASS_STAGE = { intact: 0, cracked: 1, crazed: 2, shattered: 3 } as const;
/** A window's load per hit is (km/h of the push - `v0`)² times its share, summed over hits; it steps through the glass
 *  stages at `steps`. Wreckfest 2 (damage-study.md Part 2): side glass goes from side and roll loads (four 16-25 km/h
 *  side hits and a roll onto the side left none), frontal deceleration up to 84 km/h head-on leaves the rear window
 *  whole. Side windows (tempered): a 20 km/h side knock cracks, three at 25 km/h or one at 35 shatter. The windscreen
 *  (laminated) and the rear window take roof hits fully, a quarter of a side hit at their end, and only hard frontal or
 *  rear crashes (8 % of (km/h - 50)²): the windscreen stands about twice the side glass. */
export const GLASS_LOAD: Record<WindowName, { v0: number; steps: readonly [number, number, number] }> = {
  windscreen: { v0: 10, steps: [200, 450, 800] }, rearWindow: { v0: 10, steps: [150, 400, 700] },
  frontLeft: { v0: 10, steps: [100, 300, 600] }, rearLeft: { v0: 10, steps: [100, 300, 600] },
  frontRight: { v0: 10, steps: [100, 300, 600] }, rearRight: { v0: 10, steps: [100, 300, 600] },
};
/** Where the windows sit along the car (fraction of the length from the rear; a five-door hatchback or saloon): the
 *  share of a side hit a window takes falls off with its distance from the contact, as the crumple's footprint does. */
const WINDOW_F: Record<WindowName, number> = { windscreen: .66, rearWindow: .06, frontLeft: .52, rearLeft: .28, frontRight: .52, rearRight: .28 };
/** Surface wear: a 16 x 16 unwrap of the damage box into cells, per cell a scratch/paint-loss level (`wear`: light
 *  scratches first, bare metal beyond half) and a dirt level, 0..1 each. Rows 0-3 the left (+X) side and 4-7 the
 *  right side (16 along the length x 4 up), 8-13 the roof (16 along x 6 across), 14-15 the ends (front in columns
 *  0-7, rear in 8-15; 8 across x 2 up). The renderer samples the same unwrap (actors/fleet/damageRig.ts surfaceMask). */
export const SURFACE_SIZE = 16, SURFACE_CELLS = SURFACE_SIZE * SURFACE_SIZE;
export type SurfaceFace = 'posX' | 'negX' | 'top' | 'front' | 'rear';
/** The cell of a point on a face (fleet frame). */
export function surfaceCell(layout: DamageLayout, face: SurfaceFace, x: number, y: number, z: number): number {
  const f = clamp01((z - layout.min[2]) / layout.length), h = clamp01((y - layout.min[1]) / layout.height), a = clamp01((x - layout.min[0]) / layout.width);
  const col = (v: number, n: number) => Math.min(n - 1, Math.floor(v * n));
  if (face === 'posX' || face === 'negX') return ((face === 'posX' ? 0 : 4) + col(h, 4)) * SURFACE_SIZE + col(f, SURFACE_SIZE);
  if (face === 'top') return (8 + col(a, 6)) * SURFACE_SIZE + col(f, SURFACE_SIZE);
  return (14 + col(h, 2)) * SURFACE_SIZE + (face === 'front' ? 0 : 8) + col(a, 8);
}
const cellCentres = new WeakMap<DamageLayout, { face: SurfaceFace; at: [number, number, number] }[]>();
/** Each cell's face and centre (fleet frame), on the box surface. */
function cells(layout: DamageLayout) {
  let list = cellCentres.get(layout);
  if (list) return list;
  list = [];
  const { min, length, width, height } = layout, max = layout.max;
  for (let i = 0; i < SURFACE_CELLS; i++) {
    const row = Math.floor(i / SURFACE_SIZE), c = i % SURFACE_SIZE;
    if (row < 8) list.push({ face: row < 4 ? 'posX' : 'negX', at: [row < 4 ? max[0] : min[0], min[1] + ((row % 4) + .5) / 4 * height, min[2] + (c + .5) / SURFACE_SIZE * length] });
    else if (row < 14) list.push({ face: 'top', at: [min[0] + (row - 8 + .5) / 6 * width, max[1], min[2] + (c + .5) / SURFACE_SIZE * length] });
    else list.push({ face: c < 8 ? 'front' : 'rear', at: [min[0] + ((c % 8) + .5) / 8 * width, min[1] + (row - 14 + .5) / 2 * height, c < 8 ? max[2] : min[2]] });
  }
  cellCentres.set(layout, list);
  return list;
}
/** Adds wear and dirt to the cells of `face` within `radius` (m) of `at` (fleet frame), full at the centre; returns
 *  whether any cell changed. The worker calls it for hits (applyHit) and for scrapes along walls and on the roof
 *  (vehicle.ts). */
export function stampSurface(state: DamageState, face: SurfaceFace, at: readonly [number, number, number], radius: number, wear: number, dirt = 0): boolean {
  let changed = false;
  for (const [i, cell] of cells(state.layout).entries()) {
    if (cell.face !== face) continue;
    const d = Math.hypot(cell.at[0] - at[0], cell.at[1] - at[1], cell.at[2] - at[2]), k = 1 - smooth(d / radius);
    if (k <= 0) continue;
    const w = Math.min(1, state.surfaceWear[i] + wear * k), g = Math.min(1, state.surfaceDirt[i] + dirt * k);
    if (w !== state.surfaceWear[i] || g !== state.surfaceDirt[i]) changed = true;
    state.surfaceWear[i] = w; state.surfaceDirt[i] = g;
  }
  return changed;
}
/** A scrape along a face (developer tools; the worker's own scrapes come from contacts, vehicle.ts trackScrape). */
export interface DamageScrape { face: SurfaceFace; from: readonly [number, number, number]; to: readonly [number, number, number]; metres: number; ground?: boolean }
/** Wear and dirt of the whole line from `from` to `to` on a face sliding `metres` along something (vehicle.ts SCRAPE
 *  rates): stamps every few centimetres, each weighted so the cells along the line add up to the sliding. */
export function applyScrape(state: DamageState, scrape: DamageScrape, rates: { wearPerMetre: number; dirtPerMetre: number; grimePerMetre: number; radius: number }) {
  const length = Math.hypot(scrape.to[0] - scrape.from[0], scrape.to[1] - scrape.from[1], scrape.to[2] - scrape.from[2]);
  const steps = Math.max(1, Math.ceil(length / (rates.radius / 3))), share = Math.min(1, length / steps / rates.radius);
  for (let k = 0; k <= steps; k++) {
    const t = k / steps, at = [0, 1, 2].map(i => scrape.from[i] + (scrape.to[i] - scrape.from[i]) * t) as [number, number, number];
    stampSurface(state, scrape.face, at, rates.radius, scrape.metres * rates.wearPerMetre * share, scrape.metres * (scrape.ground ? rates.dirtPerMetre : rates.grimePerMetre) * share);
  }
  if (!state.hits) state.scraped = true;
  state.revision++;
}
const glassStage = (window: WindowName, load: number) => { const steps = GLASS_LOAD[window].steps; let n = 0; while (n < 3 && load >= steps[n]) n++; return n; };
const partState = (part: PartName, load: number) => load >= PART_LOAD[part].off ? PART_STATE.detached : load >= PART_LOAD[part].loose ? PART_STATE.loose : PART_STATE.attached;

/** One car's damage. `offsets` (xyz per node, metres) is the shape the renderer draws and multiplayer replicates;
 *  `bins` the absorbed energy per face region that saturation reads; `energy` and `hits` keep counting after the shape
 *  saturates (hooks for loose parts). `wear` is the mechanical systems' energy, (km/h)² beyond the crumple threshold
 *  under each system, which never saturates (Wreckfest 2: the shape stops at the hard stop, the part states keep
 *  stepping up per hit); `steerSide` the way a bent steering pulls (+1 the car's left, -1 its right, 0 not yet
 *  bent). `revision` changes with every applied hit. */
export class DamageState {
  readonly offsets = new Float32Array(DAMAGE_NODES * 3);
  readonly bins = new Float32Array(DAMAGE_BINS);
  readonly wear = new Float32Array(WEAR_SYSTEMS);
  /** Per part (PARTS order): its load ((km/h)², PART_LOAD), its state (PART_STATE) and the end that comes free first
   *  (+1 the car's left, -1 its right; bumpers hang from the other end, set when the part first works loose). */
  readonly partLoad = new Float32Array(PART_COUNT);
  readonly parts = new Uint8Array(PART_COUNT);
  readonly partSide = new Int8Array(PART_COUNT);
  /** Per window (WINDOWS order): its load ((km/h)², GLASS_LOAD) and its stage (GLASS_STAGE). */
  readonly glassLoad = new Float32Array(WINDOW_COUNT);
  readonly glass = new Uint8Array(WINDOW_COUNT);
  /** Per surface cell (SURFACE_CELLS): scratch/paint-loss and dirt, 0..1. */
  readonly surfaceWear = new Float32Array(SURFACE_CELLS);
  readonly surfaceDirt = new Float32Array(SURFACE_CELLS);
  /** Worn by scrapes alone (no hit yet): still a damaged car to draw and send. */
  scraped = false;
  energy = 0; hits = 0; revision = 0; steerSide = 0;
  readonly layout: DamageLayout;
  constructor(layout: DamageLayout) { this.layout = layout; }
  get damaged() { return this.hits > 0 || this.scraped; }
  clear() {
    this.offsets.fill(0); this.bins.fill(0); this.wear.fill(0); this.partLoad.fill(0); this.parts.fill(0); this.partSide.fill(0);
    this.glassLoad.fill(0); this.glass.fill(0); this.surfaceWear.fill(0); this.surfaceDirt.fill(0); this.scraped = false;
    this.energy = 0; this.hits = 0; this.steerSide = 0; this.revision++;
  }
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
        // The rigid end (full falloff) crumples unevenly, up to a quarter deeper here and there, so it never reads as
        // a clean cut; its least crushed point still defines the calibrated length loss. The band behind it stays
        // smooth, which keeps the stations in order at the hard stop.
        out[o + 2] += sign * (a * D * (a >= 1 ? 1 + .3125 * (jitter(i, f, 3) - .6) : 1) + bumper);
        // Buckle and bulge peak inside the crumple band, zero at the end face and from the cabin on.
        const band = Math.sin(Math.PI * Math.min(1, depth / (FACES[face].s1 * length)));
        if (h === levels.length - 1) out[o + 1] += .3 * band * D * jitter(i, f, 0) * (l === 1 ? 1 : .7);
        else out[o + 1] -= .03 * band * D * jitter(i, f, 1);
        if (l !== 1) out[o] += Math.sign(x - layout.laterals[1]) * .1 * band * D * jitter(i, f, 2);
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
  let loaded = false;
  for (const face of FACE_LIST) {
    const kmh = pushes[face] * KMH, raw = Math.max(0, kmh * kmh - CRUMPLE.thresholdKmh ** 2);
    if (kmh <= 0) continue;
    // Footprint share per bin. Bin centres: the laterals for the end faces, thirds of the length for the sides.
    const shares = [1, 1, 1];
    for (let b = 0; b < 3 && face !== 'top'; b++) {
      if (face === 'front' || face === 'rear') shares[b] = lateralShare(Math.abs(layout.laterals[b] - (hit.at?.[0] ?? layout.laterals[1])), halfWidth);
      else shares[b] = lateralShare(Math.abs(layout.min[2] + (b + .5) / 3 * layout.length - (hit.at?.[2] ?? (layout.min[2] + layout.max[2]) / 2)),
        halfWidth + layout.length / 6);
    }
    loaded = partHit(state, face, kmh, shares, hit) || loaded;
    loaded = glassHit(state, face, kmh, hit) || loaded;
    loaded = surfaceHit(state, face, kmh, hit) || loaded;
    const e = raw / FACES[face].stiffness;
    const eBumper = face === 'front' || face === 'rear' ? Math.max(0, kmh * kmh - CRUMPLE.bumperKmh ** 2) : 0;
    if (e <= 0 && eBumper <= 0) continue;
    total += e; absorbed = true;
    const across = face === 'front' || face === 'rear' ? layout.length : face === 'top' ? layout.height : layout.width;
    const stop = FACES[face].stop * across;
    for (let b = 0; b < (face === 'top' ? 1 : 3); b++) {
      const index = BIN_OFFSET[face] + b, depth = faceDepth(face, state.bins[index], layout);
      state.bins[index] += e * shares[b] * Math.max(0, 1 - depth / stop);
      added = Math.max(added, faceDepth(face, state.bins[index], layout) - depth);
      if (face === 'front' || face === 'rear') state.bins[BUMPER_OFFSET[face] + b] += eBumper * shares[b];
    }
    if (raw > 0) wearHit(state, face, raw, shares, hit);
  }
  // A hit too light to deform anything may still knock a mirror: it counts as a hit, with the shape unchanged.
  if (!absorbed) { if (loaded) { state.hits++; state.revision++; } return 0; }
  const after = new Float32Array(DAMAGE_NODES * 3);
  shape(layout, state.bins, after);
  for (let i = 0; i < after.length; i++) state.offsets[i] = clampOffset(state.offsets[i] + after[i] - before[i]);
  state.energy += total; state.hits++; state.revision++;
  return added;
}

/** A face's push (km/h) on the parts it carries: the bumper beam takes its most loaded lateral (one beam), the bonnet
 *  and boot lid the centre-weighted bay, a mirror the side's middle or front third (and half of a roof hit, a roll).
 *  A part's state only ever moves on (there is no repair). */
function partHit(state: DamageState, face: Face, kmh: number, shares: readonly number[], hit: DamageHit): boolean {
  let loaded = false;
  const add = (part: PartName, share: number) => {
    const i = PARTS.indexOf(part), over = kmh - PART_LOAD[part].v0;
    if (over <= 0 || share <= 0 || state.parts[i] === PART_STATE.detached) return;
    state.partLoad[i] += over * over * share; loaded = true;
    const next = partState(part, state.partLoad[i]);
    if (next === state.parts[i]) return;
    if (!state.partSide[i]) {
      // The end that took more of the hit comes free first; a square hit picks a deterministic end.
      const lean = face === 'front' || face === 'rear' ? shares[2] - shares[0] + (hit.at?.[0] ?? 0) / state.layout.width : 0;
      state.partSide[i] = Math.abs(lean) > .05 ? Math.sign(lean) : jitter(state.hits + i, 9, Math.round(kmh)) < 1 ? 1 : -1;
    }
    state.parts[i] = next;
  };
  const [right, centre, left] = shares;
  if (face === 'front') { add('frontBumper', Math.max(right, centre, left)); add('bonnet', .25 * right + .5 * centre + .25 * left); }
  else if (face === 'rear') { add('rearBumper', Math.max(right, centre, left)); add('bootLid', .25 * right + .5 * centre + .25 * left); }
  // Only the footprint itself reaches a mirror, not the panel pulled around it (lateralShare's floor of a quarter).
  else if (face === 'posX') add('mirrorLeft', (Math.max(shares[1], shares[2]) - .25) / .75);
  else if (face === 'negX') add('mirrorRight', (Math.max(shares[1], shares[2]) - .25) / .75);
  else { add('mirrorLeft', .5); add('mirrorRight', .5); }
  return loaded;
}

/** A face's push (km/h) on the windows: a side hit loads each window on that side by its distance from the contact
 *  (only the footprint itself, as for the mirrors), the windscreen and rear window a quarter of that, a hard crash on
 *  an end face a small share of its window, and a roof hit every window (half for the side glass). */
function glassHit(state: DamageState, face: Face, kmh: number, hit: DamageHit): boolean {
  let loaded = false;
  const add = (window: WindowName, share: number, over = kmh - GLASS_LOAD[window].v0) => {
    const i = WINDOWS.indexOf(window);
    if (over <= 0 || share <= 0 || state.glass[i] === GLASS_STAGE.shattered) return;
    state.glassLoad[i] += over * over * share; loaded = true;
    state.glass[i] = glassStage(window, state.glassLoad[i]);
  };
  const { min, length } = state.layout, halfWidth = hit.halfWidth ?? Infinity;
  const reach = (window: WindowName) => hit.at
    ? Math.max(0, (lateralShare(Math.abs(min[2] + WINDOW_F[window] * length - hit.at[2]), halfWidth) - .25) / .75) : 1;
  if (face === 'posX' || face === 'negX') {
    const left = face === 'posX';
    for (const window of left ? ['frontLeft', 'rearLeft'] as const : ['frontRight', 'rearRight'] as const) add(window, reach(window));
    add('windscreen', .25 * reach('windscreen')); add('rearWindow', .25 * reach('rearWindow'));
  } else if (face === 'top') {
    for (const window of WINDOWS) add(window, window === 'windscreen' || window === 'rearWindow' ? 1 : .5);
  } else add(face === 'front' ? 'windscreen' : 'rearWindow', .08, kmh - 50);
  return loaded;
}

/** A hit scuffs the paint where it struck: from 12 km/h light scratches, bare metal from about 50 km/h, over the
 *  footprint (a wall: the whole face at bumper height; the roof: all of it, with dirt from the ground). */
function surfaceHit(state: DamageState, face: Face, kmh: number, hit: DamageHit): boolean {
  const wear = Math.min(1, Math.max(0, (kmh - 12) / 45));
  if (wear <= 0) return false;
  const { min, max, length, width, height } = state.layout, at = hit.at, wide = !Number.isFinite(hit.halfWidth ?? Infinity);
  if (face === 'top') return stampSurface(state, 'top', [(min[0] + max[0]) / 2, max[1], (min[2] + max[2]) / 2], length, wear * .8, wear * .6);
  if (face === 'front' || face === 'rear') {
    const z = face === 'front' ? max[2] : min[2], y = min[1] + .3 * height;
    return stampSurface(state, face, [wide ? (min[0] + max[0]) / 2 : at?.[0] ?? 0, y, z], wide ? width : Math.max(.35, (hit.halfWidth ?? .3) * 1.6), wear);
  }
  const x = face === 'posX' ? max[0] : min[0];
  return stampSurface(state, face, [x, min[1] + .45 * height, wide ? (min[2] + max[2]) / 2 : at?.[2] ?? 0], wide ? length : Math.max(.45, (hit.halfWidth ?? .3) * 1.8), wear);
}

/** A face's share of a hit on the mechanical systems behind it. `raw`: (km/h)² beyond the crumple threshold, the
 *  energy measure the crumple uses before the face's stiffness; `shares`: the footprint share per bin (end faces by
 *  lateral -X, centre, +X; sides by thirds rear, middle, front). The engine bay is at the front: its radiator and
 *  engine span most of the width, the steering rack reaches both front wheels, and each corner takes the end and the
 *  side bins over it (a side hit at a wheel is the classic way to bend one). */
function wearHit(state: DamageState, face: Face, raw: number, shares: readonly number[], hit: DamageHit) {
  const w = state.wear, [right, centre, left] = shares, c = WEAR.corners;
  let steering = 0;
  if (face === 'front') {
    const bay = .25 * right + .5 * centre + .25 * left;
    w[WEAR.cooling] += raw * bay; w[WEAR.engine] += raw * bay;
    w[c] += raw * left; w[c + 1] += raw * right;
    steering = raw * .5 * (left + right);
  } else if (face === 'rear') { w[c + 2] += raw * left; w[c + 3] += raw * right; }
  else if (face === 'posX' || face === 'negX') {
    // posX is the car's left side; bins: rear, middle, front third.
    const [rear, , front] = shares, side = face === 'posX' ? 0 : 1;
    w[c + side] += raw * front; w[c + 2 + side] += raw * rear;
    w[WEAR.engine] += raw * .25 * front;
    steering = raw * .5 * front;
  }
  if (steering <= 0) return;
  w[WEAR.steering] += steering;
  // A bent steering pulls towards the side that took the hit; a square hit bends it whichever way the first one did.
  if (!state.steerSide) {
    const lateral = face === 'posX' ? 1 : face === 'negX' ? -1 : (hit.at?.[0] ?? 0) / (state.layout.width / 2);
    state.steerSide = Math.abs(lateral) > .1 ? Math.sign(lateral) : jitter(state.hits, 5, Math.round(raw)) < 1 ? 1 : -1;
  }
}

/** Predicted bounding-box length loss (m) of `hits` identical head-on wall hits at `kmh`: the least crushed point of
 *  the front station (structural depth plus the bumper layer), as the renderer's deformed bounds measure it. For tests
 *  and calibration. */
export function headOnLoss(layout: DamageLayout, kmh: number, hits = 1): number {
  const state = new DamageState(layout);
  for (let i = 0; i < hits; i++) applyHit(state, { dv: [0, 0, -kmh / KMH] });
  // The bounding box ends at the least crushed point of the front station.
  const front = layout.stations.length - 1;
  return -Math.max(...[0, 1].flatMap(h => [0, 1, 2].map(l => state.offsets[nodeIndex(front, h, l) * 3 + 2])));
}

// ------------------------------------------------------------------ compact state (worker → renderer, multiplayer, saves)
/** Offsets are quantized to 5 mm in int8 (±63.5 cm), bins, wear and part loads to 8 (km/h)² in uint16. */
const OFFSET_UNIT = .005, BIN_UNIT = 8, WIRE_VERSION = 5;
const clampOffset = (v: number) => Math.max(-127 * OFFSET_UNIT, Math.min(127 * OFFSET_UNIT, v));
const HEADER = 8, SHAPE_BYTES = HEADER + DAMAGE_BINS * 2 + DAMAGE_NODES * 3, MECHANICS_BYTES = SHAPE_BYTES + WEAR_SYSTEMS * 2 + 2;
/** Version 3 adds the parts: per part its state and free end (one byte: state in bits 0-1, bit 2 a side is set, bit 3
 *  it is the left) and its load (uint16). Version 2 (224 bytes: the wear as uint16 per system, the steering side as
 *  int8 and a spare byte) and version 1 (body only, 208 bytes) still read, with unworn systems and attached parts. */
const PARTS_BYTES = MECHANICS_BYTES + PART_COUNT * 3;
/** Version 4 adds the glass: per window its stage (uint8) and its load (uint16); version 5 the surface: per cell its
 *  wear (high nibble) and dirt (low nibble), in 15ths. */
const GLASS_BYTES = PARTS_BYTES + WINDOW_COUNT * 3;
export const DAMAGE_WIRE_BYTES = GLASS_BYTES + SURFACE_CELLS;
const WIRE_LENGTHS = [0, SHAPE_BYTES, MECHANICS_BYTES, PARTS_BYTES, GLASS_BYTES, DAMAGE_WIRE_BYTES];
const nibble = (v: number) => Math.max(0, Math.min(15, Math.round(v * 15)));

/** Wire form of a damage state: base64 of version, node count, hits, energy (64 (km/h)² units), bins, offsets, wear,
 *  steering side, parts, glass, surface. */
export function encodeDamage(state: DamageState): string {
  const bytes = new Uint8Array(DAMAGE_WIRE_BYTES), view = new DataView(bytes.buffer);
  view.setUint8(0, WIRE_VERSION); view.setUint8(1, DAMAGE_NODES); view.setUint16(2, Math.min(65535, state.hits), true);
  view.setUint32(4, Math.min(0xffffffff, Math.round(state.energy / 64)), true);
  for (let i = 0; i < DAMAGE_BINS; i++) view.setUint16(HEADER + i * 2, Math.min(65535, Math.round(state.bins[i] / BIN_UNIT)), true);
  const base = HEADER + DAMAGE_BINS * 2;
  for (let i = 0; i < DAMAGE_NODES * 3; i++) view.setInt8(base + i, Math.round(state.offsets[i] / OFFSET_UNIT));
  for (let i = 0; i < WEAR_SYSTEMS; i++) view.setUint16(SHAPE_BYTES + i * 2, Math.min(65535, Math.round(state.wear[i] / BIN_UNIT)), true);
  view.setInt8(SHAPE_BYTES + WEAR_SYSTEMS * 2, state.steerSide);
  for (let i = 0; i < PART_COUNT; i++) {
    const side = state.partSide[i];
    view.setUint8(MECHANICS_BYTES + i, state.parts[i] | (side ? 4 : 0) | (side > 0 ? 8 : 0));
    view.setUint16(MECHANICS_BYTES + PART_COUNT + i * 2, Math.min(65535, Math.round(state.partLoad[i] / BIN_UNIT)), true);
  }
  for (let i = 0; i < WINDOW_COUNT; i++) {
    view.setUint8(PARTS_BYTES + i, state.glass[i]);
    view.setUint16(PARTS_BYTES + WINDOW_COUNT + i * 2, Math.min(65535, Math.round(state.glassLoad[i] / BIN_UNIT)), true);
  }
  for (let i = 0; i < SURFACE_CELLS; i++) bytes[GLASS_BYTES + i] = nibble(state.surfaceWear[i]) << 4 | nibble(state.surfaceDirt[i]);
  let text = '';
  for (const b of bytes) text += String.fromCharCode(b);
  return btoa(text);
}

/** Reads a wire string into `state` (its revision moves on); false and `state` unchanged for anything malformed. */
export function decodeDamage(wire: unknown, state: DamageState): boolean {
  if (typeof wire !== 'string' || wire.length > 4 * Math.ceil(DAMAGE_WIRE_BYTES / 3)) return false;
  let text: string;
  try { text = atob(wire); } catch { return false; }
  const version = WIRE_LENGTHS.indexOf(text.length);
  if (version < 1) return false;
  const bytes = Uint8Array.from(text, c => c.charCodeAt(0)), view = new DataView(bytes.buffer);
  if (view.getUint8(0) !== version || view.getUint8(1) !== DAMAGE_NODES) return false;
  const side = version > 1 ? view.getInt8(SHAPE_BYTES + WEAR_SYSTEMS * 2) : 0;
  if (side < -1 || side > 1) return false;
  for (let i = 0; version > 2 && i < PART_COUNT; i++) if ((bytes[MECHANICS_BYTES + i] & 3) > PART_STATE.detached || bytes[MECHANICS_BYTES + i] > 15) return false;
  for (let i = 0; version > 3 && i < WINDOW_COUNT; i++) if (bytes[PARTS_BYTES + i] > GLASS_STAGE.shattered) return false;
  state.hits = view.getUint16(2, true); state.energy = view.getUint32(4, true) * 64;
  for (let i = 0; i < DAMAGE_BINS; i++) state.bins[i] = view.getUint16(HEADER + i * 2, true) * BIN_UNIT;
  const base = HEADER + DAMAGE_BINS * 2;
  for (let i = 0; i < DAMAGE_NODES * 3; i++) state.offsets[i] = view.getInt8(base + i) * OFFSET_UNIT;
  for (let i = 0; i < WEAR_SYSTEMS; i++) state.wear[i] = version > 1 ? view.getUint16(SHAPE_BYTES + i * 2, true) * BIN_UNIT : 0;
  state.steerSide = side;
  for (let i = 0; i < PART_COUNT; i++) {
    const b = version > 2 ? bytes[MECHANICS_BYTES + i] : 0;
    state.parts[i] = b & 3; state.partSide[i] = b & 4 ? (b & 8 ? 1 : -1) : 0;
    state.partLoad[i] = version > 2 ? view.getUint16(MECHANICS_BYTES + PART_COUNT + i * 2, true) * BIN_UNIT : 0;
  }
  for (let i = 0; i < WINDOW_COUNT; i++) {
    state.glass[i] = version > 3 ? bytes[PARTS_BYTES + i] : 0;
    state.glassLoad[i] = version > 3 ? view.getUint16(PARTS_BYTES + WINDOW_COUNT + i * 2, true) * BIN_UNIT : 0;
  }
  let scraped = false;
  for (let i = 0; i < SURFACE_CELLS; i++) {
    const b = version > 4 ? bytes[GLASS_BYTES + i] : 0;
    state.surfaceWear[i] = (b >> 4) / 15; state.surfaceDirt[i] = (b & 15) / 15;
    scraped ||= b !== 0;
  }
  state.scraped = scraped;
  state.revision++;
  return true;
}

/** A valid wire string (servers and saves keep only these). */
export const validDamageWire = (wire: unknown) => decodeDamage(wire, new DamageState(damageLayout({ center: [0, 0, 0], half: [1, 1, 1] })));
