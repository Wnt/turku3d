// Mechanical damage of a damageable car: levels of its systems from the wear a crash adds (vehicle/damage.ts
// wearHit), and what they do to the car. Headless and shared: the physics worker applies the drive and handling
// effects (vehicle/vehicle.ts), the renderer the engine-bay smoke, the audio the rough engine and the HUD the levels,
// all from the same damage wire. Calibration: ~/turku3d/research/wreckfest2/damage-study.md, docs in
// vehicle/README.md "Mechanical damage".
import { DamageState, PART_STATE, WEAR, decodeDamage, damageLayout } from './damage';

/** Five states per system as in Wreckfest 2: 0 intact, 1-3 damaged, 4 terminal. A level is the number of rungs its
 *  wear has passed; wear is (km/h)² beyond the 35 km/h crumple threshold under the system, summed over hits. Fitted to
 *  the Wreckfest 2 head-on and repeat-hit tables (Normal damage): the radiator from one ~75-80 km/h hit, the second at
 *  61.5 or the seventh at 42.5 km/h (none at 72 km/h or six at 42.5), its level 2 at the fourth hit at 61.5; the
 *  front zone on the hit side steps 1 → 4 over hits 2-5 at 61.5 km/h; the engine at the sixth; suspension and
 *  steering hardware intact through six hits at 61.5 km/h (their wheel alignment changes earlier, see `alignment`). */
export const LADDERS = {
  cooling: [4000, 10000, 18000, 28000],
  engine: [15000, 24000, 34000, 46000],
  steering: [16000, 26000, 38000, 52000],
  suspension: [16000, 26000, 38000, 52000],
  /** The body zone at each corner (Wreckfest's front armour FL/FR, and the same rule at the rear). */
  zone: [4000, 7500, 10000, 12500],
} as const;
export const MAX_LEVEL = 4;

/** Per level: the engine's share of power with a damaged radiator (a leak runs it hot) and with a damaged engine; the
 *  idle roughness (misfires, 0..1) and the engine-bay steam and smoke (0..1). The car always keeps a third of its power:
 *  it stays drivable on this setting. */
const EFFECTS = {
  coolingPower: [1, .95, .88, .8, .7],
  enginePower: [1, .88, .76, .62, .5],
  coolingRough: [0, .1, .2, .35, .5],
  engineRough: [0, .3, .55, .8, 1],
  steam: [0, .35, .6, .85, 1],
  smoke: [0, .3, .55, .8, 1],
} as const;
const MIN_POWER = 1 / 3;

/** Wheel alignment, which changes before any hardware level (Wreckfest 2 after one 80 km/h frontal hit: a mild pull of
 *  -0.5 to -1.0° heading per 2.5 s of straight full reverse against -0.14° fresh, camber within 0.35°, no wheel
 *  off). The steering pulls towards `steerSide` by `steer` rad once its wear passes `w0` ((km/h)²), saturating at
 *  `max`; each corner's toe turns its wheel out (front) or in (rear) the same way, so a square hit's toe cancels across
 *  the axle and a corner hit pulls towards it. Camber is not modelled: Rapier's ray-cast wheel projects its axle onto
 *  the contact plane, so a camber angle would change nothing but the drawing, and Wreckfest's stays under 0.35°.
 *  Suspension levels lower their corner (`sag`, m: the mount moves up into the body) and soften its damping. */
export const ALIGNMENT = {
  steer: { w0: 2000, scale: 8000, max: .4 * Math.PI / 180 },
  toe: { w0: 1500, scale: 12000, max: 1 * Math.PI / 180 },
  sag: [0, .01, .02, .035, .05],
  damping: [1, .85, .7, .55, .4],
} as const;
const saturate = (wear: number, c: { w0: number; scale: number; max: number }) => c.max * (1 - Math.exp(-Math.max(0, wear - c.w0) / c.scale));

const level = (wear: number, ladder: readonly number[]) => { let n = 0; while (n < ladder.length && wear >= ladder[n]) n++; return n; };

export interface Mechanics {
  /** Levels 0..4. Corners in the physics wheel order: front left, front right, rear left, rear right. */
  readonly cooling: number; readonly engine: number; readonly steering: number;
  readonly suspension: readonly number[]; readonly zones: readonly number[];
  /** Share of the engine's force and power (1 intact). */
  readonly power: number;
  /** Idle roughness, engine-bay steam and smoke, 0..1. */
  readonly rough: number; readonly steam: number; readonly smoke: number;
  /** Highest level of any system or zone: 0 for an intact car (the HUD shows nothing). */
  readonly worst: number;
  /** Steering offset of the steered wheels and toe per wheel (rad, + turns the wheel to the car's left). */
  readonly steer: number; readonly toe: readonly number[];
  /** Per corner: how far its mount moved up into the body (m) and its damping factor. */
  readonly sag: readonly number[]; readonly damping: readonly number[];
  /** Body parts hanging loose (vehicle/damage.ts PARTS): they rattle. */
  readonly loose: number;
}

export const INTACT: Mechanics = { cooling: 0, engine: 0, steering: 0, suspension: [0, 0, 0, 0], zones: [0, 0, 0, 0],
  power: 1, rough: 0, steam: 0, smoke: 0, worst: 0, steer: 0, toe: [0, 0, 0, 0], sag: [0, 0, 0, 0], damping: [1, 1, 1, 1], loose: 0 };

/** The mechanical state of a damage state. */
export function mechanics(state: DamageState): Mechanics {
  if (!state.damaged) return INTACT;
  const w = state.wear, corners = [0, 1, 2, 3].map(i => w[WEAR.corners + i]);
  const cooling = level(w[WEAR.cooling], LADDERS.cooling), engine = level(w[WEAR.engine], LADDERS.engine);
  const steering = level(w[WEAR.steering], LADDERS.steering);
  const suspension = corners.map(x => level(x, LADDERS.suspension)), zones = corners.map(x => level(x, LADDERS.zone));
  return { cooling, engine, steering, suspension, zones,
    power: Math.max(MIN_POWER, EFFECTS.coolingPower[cooling] * EFFECTS.enginePower[engine]),
    rough: Math.max(EFFECTS.coolingRough[cooling], EFFECTS.engineRough[engine]),
    steam: EFFECTS.steam[cooling], smoke: EFFECTS.smoke[engine],
    worst: Math.max(cooling, engine, steering, ...suspension, ...zones),
    steer: state.steerSide * saturate(w[WEAR.steering], ALIGNMENT.steer),
    // Out at the front (the left wheel turns left), in at the rear; corners 0 and 2 are on the car's left.
    toe: corners.map((x, i) => (i % 2 ? -1 : 1) * (i < 2 ? 1 : -1) * saturate(x, ALIGNMENT.toe)),
    sag: suspension.map(n => ALIGNMENT.sag[n]), damping: suspension.map(n => ALIGNMENT.damping[n]),
    loose: state.parts.reduce((n, p) => n + Number(p === PART_STATE.loose), 0) };
}

// The mechanics of a wire state (renderer, audio, HUD: every frame, for the local and the remote cars), decoded once
// per distinct wire. The wire layout is the same for every cage, so one scratch state serves all models.
const scratch = new DamageState(damageLayout({ center: [0, 0, 0], half: [1, 1, 1] }));
const cache = new Map<string, Mechanics>();
export function mechanicsOf(wire: string | null | undefined): Mechanics {
  if (!wire) return INTACT;
  let m = cache.get(wire);
  if (!m) {
    m = decodeDamage(wire, scratch) ? mechanics(scratch) : INTACT;
    if (cache.size >= 32) cache.clear();
    cache.set(wire, m);
  }
  return m;
}
