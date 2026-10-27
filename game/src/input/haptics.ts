// Controller rumble from what the player feels: wheel bumps, curb strikes, collisions, landings, stairs and engine
// load. The mappers are pure (impulse or speed -> strength and duration); `Haptics` deduplicates the physics events,
// mixes one transient over the continuous engine rumble and hands each effect to `Input.rumble` (README "Haptics").
import { VEHICLES, type VehicleKind } from '../physics/constants';
import type { BumpState, VehicleInput, VehicleState } from '../physics/protocol';
import { MAT_FLAG, materialHasFlag } from '../shared/col/materials';
import { curbResponse } from '../vehicle/curbStrike';
import { clamp } from './bindings';

/** One dual-rumble effect: duration in ms, motor magnitudes 0..1 (strong: low-frequency, weak: high-frequency). */
export interface Rumble { duration: number; strong: number; weak: number }
type RumbleCause = 'bump' | 'curb' | 'impact' | 'landing' | 'stairs' | 'engine';
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
/** The engine rumble is renewed this often with effects of ENGINE_PULSE_MS, so consecutive pulses overlap. */
export const ENGINE_REFRESH_MS = 100;
export const ENGINE_PULSE_MS = 150;
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
  /** Effects sent per cause since load; `log` keeps the latest LOG_SIZE transient effects (engine pulses excluded). */
  readonly counts: Record<RumbleCause, number> = { bump: 0, curb: 0, impact: 0, landing: 0, stairs: 0, engine: 0 };
  readonly log: RumbleRecord[] = [];
  private seen = { vehicle: null as string | null, curb: 0, bump: 0, impact: 0 };
  private pending: { cause: RumbleCause; rumble: Rumble }[] = [];
  private grounded = true;
  private fall = 0;
  private until = -Infinity;
  private playing: Rumble | null = null;
  private engineAt = -Infinity;

  constructor(private readonly play: (rumble: Rumble) => void) {}

  /** A footstep (audio/gameplay.ts stride events) on `material`. */
  footstep(material: number) {
    if (materialHasFlag(material, MAT_FLAG.STAIR)) this.pending.push({ cause: 'stairs', rumble: STAIR_STEP });
  }

  update(frame: HapticFrame) {
    const v = frame.vehicle;
    if (!frame.active) {
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

  /** The strongest new transient plays unless a stronger one is still running; the engine fills the gaps. */
  private mix(frame: HapticFrame, v: VehicleState | null) {
    const input = frame.input;
    const throttle = input ? Math.abs(input.throttle) + ((v?.forwardSpeed ?? 0) < 0 ? input.brake : 0) : 0;
    const engine = v ? engineRumble(v.kind ?? 'car', throttle, v.forwardSpeed) : null;
    const power = (r: Rumble) => r.strong + r.weak;
    const next = this.pending.reduce<{ cause: RumbleCause; rumble: Rumble } | null>(
      (best, row) => !best || power(row.rumble) > power(best.rumble) ? row : best, null);
    this.pending = [];
    const now = frame.now;
    if (next && (now >= this.until || !this.playing || power(next.rumble) > power(this.playing))) {
      const r = { duration: next.rumble.duration, strong: Math.max(next.rumble.strong, engine?.strong ?? 0),
        weak: Math.max(next.rumble.weak, engine?.weak ?? 0) };
      this.play(r);
      this.playing = r; this.until = now + r.duration;
      this.counts[next.cause]++;
      this.log.push({ time: now, cause: next.cause, ...r });
      if (this.log.length > LOG_SIZE) this.log.shift();
    } else if (engine && now >= this.until && now - this.engineAt >= ENGINE_REFRESH_MS) {
      this.play({ duration: ENGINE_PULSE_MS, ...engine });
      this.engineAt = now;
      this.counts.engine++;
    }
  }
}
