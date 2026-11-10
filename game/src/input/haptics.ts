// Controller rumble from what the player feels: wheel bumps, curb strikes, collisions, landings, stairs, engine load
// and rough paving under the tyres. The mappers are pure (impulse or speed -> strength and duration); `Haptics`
// deduplicates the physics events, mixes one transient over the continuous layer (engine, horn, surface) and hands
// each effect to `Input.rumble` (README "Haptics").
import { VEHICLES, type VehicleKind } from '../physics/constants';
import type { BumpState, VehicleInput, VehicleState, WheelState } from '../physics/protocol';
import { MAT, MAT_FLAG, materialHasFlag } from '../shared/col/materials';
import { curbResponse } from '../vehicle/curbStrike';
import { clamp } from './bindings';

/** One dual-rumble effect: duration in ms, motor magnitudes 0..1 (strong: low-frequency, weak: high-frequency). */
export interface Rumble { duration: number; strong: number; weak: number }
type RumbleCause = 'bump' | 'curb' | 'impact' | 'landing' | 'stairs' | 'engine' | 'surface';
type Level = Omit<Rumble, 'duration'>;
interface RumbleRecord extends Rumble { time: number; cause: RumbleCause }

const ramp = (value: number, from: number, to: number) => clamp((value - from) / (to - from), 0, 1);

/** Step in a wheel's ground path (m) felt as a kerb: lidar kerbs read 10–17 cm, road texture and grade changes
 *  stay below. Suspension compression speed (m/s) felt as a jolt, and the speed of a full-strength jolt. */
export const STEP_FELT = 0.07;
export const JOLT_FELT = 1;
const JOLT_FULL = 4;
/** A drop off a kerb is felt this much weaker than the same step up. */
const DROP_FACTOR = 0.7;

/** A wheel bump (`VehicleState.bump`). A step in the ground path is felt like a curb strike of that height at that
 *  speed (curbRumble), so lidar kerbs and the synthetic scene's curb faces match; a suspension jolt (humps, potholes,
 *  landings) maps through JOLT_FELT..JOLT_FULL. The stronger of the two plays. */
export function bumpRumble(bump: Pick<BumpState, 'height' | 'jolt' | 'speed'>): Rumble | null {
  const step = Math.abs(bump.height) >= STEP_FELT ? curbRumble(curbStrength(bump.speed, bump.height)) : null;
  const jolt = bump.jolt >= JOLT_FELT ? ramp(bump.jolt, JOLT_FELT, JOLT_FULL) : null;
  const shake = jolt === null ? null : { duration: 70 + 80 * jolt, strong: 0.2 + 0.6 * jolt, weak: 0.12 + 0.5 * jolt };
  if (!step || (shake && shake.strong > step.strong)) return shake;
  return step;
}

/** The curb-strike module's rumble strength (speed x height), with drops felt DROP_FACTOR as strongly. */
export function curbStrength(speed: number, height: number) {
  const s = curbResponse(Math.abs(speed), Math.abs(height), 0).rumble;
  return height < 0 ? s * DROP_FACTOR : s;
}

/** A curb-material strike (`VehicleState.curbStrike.rumble`, speed x height): the synthetic scene's 12 cm kerbs. */
export function curbRumble(strength: number): Rumble {
  const s = clamp(strength, 0, 1);
  return { duration: 120, strong: s, weak: s * 0.6 };
}

/** Collision speed change (impulse / mass, m/s) that rumbles, and the speed change of a full-strength crash. */
const IMPACT_FELT = 0.5;
const IMPACT_FULL = 8;
export function impactRumble(impulse: number, mass: number): Rumble | null {
  const deltaV = impulse / mass;
  if (deltaV < IMPACT_FELT) return null;
  const s = ramp(deltaV, IMPACT_FELT, IMPACT_FULL);
  return { duration: 120 + 230 * s, strong: 0.35 + 0.65 * s, weak: 0.5 + 0.5 * s };
}

/** Fall speed (m/s) at touchdown that rumbles: a jump (4.6 m/s) does, walking down a slope or a step does not. */
const LANDING_FELT = 2;
const LANDING_FULL = 10;
export function landingRumble(fallSpeed: number): Rumble | null {
  if (fallSpeed < LANDING_FELT) return null;
  const s = ramp(fallSpeed, LANDING_FELT, LANDING_FULL);
  return { duration: 70 + 110 * s, strong: 0.15 + 0.65 * s, weak: 0.1 + 0.4 * s };
}

/** A footstep on a stair material: one light tick per step. */
export const STAIR_STEP: Rumble = { duration: 45, strong: 0.1, weak: 0.08 };

/** Continuous engine rumble: a car under throttle (strongest pulling away), a scooter's single cylinder whenever it
 *  is ridden. Weak motor only, so every transient above stays distinct. Null when the engine gives nothing to feel. */
export function engineRumble(kind: VehicleKind, throttle: number, speed: number): Omit<Rumble, 'duration'> | null {
  const t = clamp(throttle, 0, 1), v = clamp(Math.abs(speed) / VEHICLES[kind].topSpeed, 0, 1);
  if (kind === 'scooter') return { strong: 0, weak: 0.05 + 0.08 * t + 0.05 * v };
  if (t < 0.05) return null;
  return { strong: 0, weak: t * (0.06 + 0.08 * (1 - v)) };
}
/** Roughness (0..1) of the collision materials a tyre feels as a constant rattle. Cobblestone is every sett and
 *  cobble surface of the city: the generators map the street register's noppakivi, nupukivi, luonnonkivi, kenttäkivi
 *  and graniitti and OSM's sett, cobblestone and unhewn_cobblestone to it (Kauppatori, Portsa, the riverside).
 *  Concrete pavers, brick and slabs (paving_slab) are laid flat and stay silent. */
export const SURFACE_ROUGHNESS: Readonly<Record<number, number>> = { [MAT.cobblestone]: 1 };
/** The surface rumble starts above SURFACE_FELT_KMH and is at full strength from SURFACE_FULL_KMH. */
export const SURFACE_FELT_KMH = 5;
export const SURFACE_FULL_KMH = 50;
/** Strength just above SURFACE_FELT_KMH (so the start is felt), as a share of full strength. */
const SURFACE_FLOOR = 0.15;
/** Full strength per motor: the high-frequency motor carries the rapid buzz of the setts, the low-frequency one
 *  gives it body and leaves room for kerbs, jolts and crashes to come through on top. */
const SURFACE_FULL: Level = { strong: 0.55, weak: 1 };

/** The share of the vehicle's wheels rolling on a rough material, weighted by its roughness: 1 with every wheel on
 *  cobbles, 0.25 with one of four in a cobbled gutter. A wheel in the air counts as off. */
export function roughShare(wheels: readonly Pick<WheelState, 'contact' | 'tyre'>[]): number {
  if (!wheels.length) return 0;
  let sum = 0;
  for (const w of wheels) if (w.contact && w.tyre) sum += SURFACE_ROUGHNESS[w.tyre.material] ?? 0;
  return sum / wheels.length;
}

/** Continuous rumble of rough paving: `speed` in m/s, `share` from roughShare. Null at or below SURFACE_FELT_KMH and
 *  off the paving; it rises linearly to SURFACE_FULL at SURFACE_FULL_KMH and stays there. */
export function surfaceRumble(speed: number, share: number): Level | null {
  const kmh = Math.abs(speed) * 3.6;
  if (kmh <= SURFACE_FELT_KMH || share <= 0) return null;
  const level = (SURFACE_FLOOR + (1 - SURFACE_FLOOR) * ramp(kmh, SURFACE_FELT_KMH, SURFACE_FULL_KMH)) * clamp(share, 0, 1);
  return { strong: SURFACE_FULL.strong * level, weak: SURFACE_FULL.weak * level };
}

/** The continuous layer (engine, horn, surface) is renewed this often with effects of CONTINUOUS_PULSE_MS, so
 *  consecutive pulses overlap even over a few slow frames; it is cut with a zero effect the moment it ends. */
export const CONTINUOUS_REFRESH_MS = 100;
export const CONTINUOUS_PULSE_MS = 250;
/** The zero effect that cuts whatever still plays. */
export const SILENCE: Rumble = { duration: 0, strong: 0, weak: 0 };
const loudest = (a: Level | null, b: Level | null): Level | null =>
  a && b ? { strong: Math.max(a.strong, b.strong), weak: Math.max(a.weak, b.weak) } : a ?? b;
const LOG_SIZE = 256;

export interface HapticFrame {
  /** performance.now() in ms. */
  now: number;
  /** False while paused, on the start screen or in a menu: nothing rumbles and events seen meanwhile never replay. */
  active: boolean;
  /** The driven vehicle (null on foot) and its id; a new id starts without replaying that vehicle's old events. */
  vehicle: VehicleState | null;
  vehicleId: string | null;
  input: VehicleInput | null;
  /** The character on foot (null in a vehicle): vertical velocity in m/s. */
  character: { grounded: boolean; vy: number } | null;
}

export class Haptics {
  /** Effects sent per cause since load (`engine`: every continuous pulse; `surface`: those carrying a surface
   *  rumble); `log` keeps the latest LOG_SIZE transient effects (continuous pulses excluded). */
  readonly counts: Record<RumbleCause, number> = { bump: 0, curb: 0, impact: 0, landing: 0, stairs: 0, engine: 0, surface: 0 };
  readonly log: RumbleRecord[] = [];
  private seen = { vehicle: null as string | null, curb: 0, bump: 0, impact: 0 };
  private pending: { cause: RumbleCause; rumble: Rumble }[] = [];
  private grounded = true;
  private fall = 0;
  private until = -Infinity;
  private playing: Rumble | null = null;
  private continuousAt = -Infinity;
  private last = -Infinity;
  /** The surface rumble of the latest frame (null off rough paving, too slow, on foot or inactive) and the
   *  continuous level last sent to the pad (null while silent): what a test or the console reads. */
  surface: (Level & { share: number; kmh: number }) | null = null;
  continuous: Level | null = null;

  constructor(private readonly play: (rumble: Rumble) => void) {}

  /** A footstep (audio/gameplay.ts stride events) on `material`. */
  footstep(material: number) {
    if (materialHasFlag(material, MAT_FLAG.STAIR)) this.pending.push({ cause: 'stairs', rumble: STAIR_STEP });
  }

  update(frame: HapticFrame) {
    const v = frame.vehicle;
    if (!frame.active) {
      this.silence(frame.now);
      this.surface = null;
      this.pending = [];
      this.seed(v, frame.vehicleId);
      this.grounded = true; this.fall = 0;
      return;
    }
    if (v && frame.vehicleId === this.seen.vehicle) this.vehicleEvents(v);
    else this.seed(v, frame.vehicleId);
    const c = frame.character;
    if (c && !c.grounded) this.fall = Math.max(this.fall, -c.vy);
    else {
      if (c && !this.grounded) this.add('landing', landingRumble(this.fall));
      this.fall = 0;
    }
    this.grounded = c?.grounded ?? true;
    this.mix(frame, v);
  }

  private vehicleEvents(v: VehicleState) {
    const kind = v.kind ?? 'car';
    if (v.curbStrike && v.curbStrike.id !== this.seen.curb) {
      this.seen.curb = v.curbStrike.id;
      this.add('curb', curbRumble(v.curbStrike.rumble));
    }
    if (v.bump && v.bump.id !== this.seen.bump) {
      this.seen.bump = v.bump.id;
      this.add('bump', bumpRumble(v.bump));
    }
    if (v.impact && v.impact.id !== this.seen.impact) {
      this.seen.impact = v.impact.id;
      this.add('impact', impactRumble(v.impact.impulse, VEHICLES[kind].mass));
    }
  }

  private seed(v: VehicleState | null, id: string | null) {
    this.seen = { vehicle: v ? id : null, curb: v?.curbStrike?.id ?? 0, bump: v?.bump?.id ?? 0, impact: v?.impact?.id ?? 0 };
  }

  private add(cause: RumbleCause, rumble: Rumble | null) { if (rumble) this.pending.push({ cause, rumble }); }

  /** Cuts a continuous pulse or transient that may still be playing. */
  private silence(now: number) {
    if (now < this.until || now < this.continuousAt + CONTINUOUS_PULSE_MS) this.play(SILENCE);
    this.until = this.continuousAt = -Infinity;
    this.playing = this.continuous = null;
  }

  /** The strongest new transient plays unless a stronger one is still running; the continuous layer (the loudest
   *  per motor of engine, horn and surface) lies under it and fills the time between transients without a gap. */
  private mix(frame: HapticFrame, v: VehicleState | null) {
    const input = frame.input;
    const throttle = input ? Math.abs(input.throttle) + ((v?.forwardSpeed ?? 0) < 0 ? input.brake : 0) : 0;
    const engine = v ? engineRumble(v.kind ?? 'car', throttle, v.forwardSpeed) : null;
    const share = v ? roughShare(v.wheels) : 0;
    const surface = v ? surfaceRumble(v.speed, share) : null;
    this.surface = surface && v ? { ...surface, share, kmh: Math.abs(v.speed) * 3.6 } : null;
    const horn = frame.input?.horn ? { strong: .08, weak: .15 } : null;
    const continuous = loudest(loudest(engine, horn), surface);
    const power = (r: Level) => r.strong + r.weak;
    const next = this.pending.reduce<{ cause: RumbleCause; rumble: Rumble } | null>(
      (best, row) => !best || power(row.rumble) > power(best.rumble) ? row : best, null);
    this.pending = [];
    const now = frame.now;
    // A transient hands over to the continuous layer one frame early, so no frame passes with the motors off.
    const handover = Math.min(Math.max(now - this.last, 0) * 1.5, CONTINUOUS_REFRESH_MS);
    this.last = now;
    if (next && (now >= this.until || !this.playing || power(next.rumble) > power(this.playing))) {
      const under = loudest(engine, surface);
      const r = { duration: next.rumble.duration, strong: Math.max(next.rumble.strong, under?.strong ?? 0),
        weak: Math.max(next.rumble.weak, under?.weak ?? 0) };
      this.play(r);
      this.playing = r; this.until = now + r.duration;
      this.continuous = null; this.continuousAt = -Infinity;
      this.counts[next.cause]++;
      this.log.push({ time: now, cause: next.cause, ...r });
      if (this.log.length > LOG_SIZE) this.log.shift();
    } else if (!continuous) {
      if (this.continuous) { this.play(SILENCE); this.continuous = null; this.continuousAt = -Infinity; }
    } else if (now >= this.until - handover && now - this.continuousAt >= CONTINUOUS_REFRESH_MS) {
      this.play({ duration: CONTINUOUS_PULSE_MS, ...continuous });
      this.continuous = continuous;
      this.continuousAt = now;
      this.counts.engine++;
      if (surface) this.counts.surface++;
    }
  }
}
