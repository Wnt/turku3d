// Rear-wheel steering of the 4400 car (SPEC §3.2). Pure and Node-importable: the physics worker steps it per vehicle,
// and the tests and the autopilot read the same ratio.

/** `auto` is the passive counter-phase steer, `auto50` the same at half its ratio, `crab` the in-phase SIDEWAYS steer
 *  and `lock` centred. */
export type RearSteerMode = 'auto' | 'auto50' | 'crab' | 'lock';
/** Wire order 0..3 (MP `drv[3]`, net/drv.ts). AUTO 50% has index 2, which the retired MANUAL mode used, so AUTO, CRAB
 *  and LOCK keep the indices older clients decode. */
export const REAR_STEER_MODES: readonly RearSteerMode[] = ['auto', 'crab', 'auto50', 'lock'];
/** The player's cycle (sprint in the truck, play.ts): AUTO → AUTO 50% → LOCK → SIDEWAYS → AUTO. */
export const REAR_STEER_CYCLE: readonly RearSteerMode[] = ['auto', 'auto50', 'lock', 'crab'];
/** Share of the passive counter-phase ratio that AUTO 50% steers. */
export const AUTO50_SHARE = 0.5;

export interface RearSteerTuning {
  wheels: readonly number[];
  /** Rear inner-wheel lock (rad). */
  maxAngle: number;
  /** Full passive ratio at and below this speed (m/s). */
  fullBelow: number;
  /** The passive ratio reaches 0 and the lockout engages at this speed (m/s). */
  lockout: number;
  /** The lockout releases below this speed (m/s). */
  unlock: number;
  /** Hydraulic slew limit of the rear angle (rad/s). */
  rate: number;
  /** Front inner-wheel lock (rad): CRAB copies the front angle itself, up to `maxAngle`. Absent: `maxAngle`. */
  frontLock?: number;
}

/** `angle`: the rear inner-wheel angle (rad, + left, the chassis steer sign). */
export interface RearSteerState { mode: RearSteerMode; angle: number; locked: boolean }

export const rearSteerIdle = (mode: RearSteerMode = 'auto'): RearSteerState => ({ mode, angle: 0, locked: false });

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** 1 at or below `fullBelow`, smoothstep down to 0 at `lockout`. */
export function passiveRatio(t: RearSteerTuning, speed: number): number {
  const u = clamp((Math.abs(speed) - t.fullBelow) / Math.max(1e-6, t.lockout - t.fullBelow), 0, 1);
  return 1 - u * u * (3 - 2 * u);
}

/** The rear angle the mode asks for before the slew limit. `frontInner01` is the signed front inner angle as a
 *  fraction of the front lock (−1..1). */
export function rearSteerTarget(t: RearSteerTuning, s: RearSteerState, frontInner01: number, speed: number): number {
  if (s.locked || s.mode === 'lock') return 0;
  const front = clamp(frontInner01, -1, 1);
  if (s.mode === 'crab') return clamp(front * (t.frontLock ?? t.maxAngle), -t.maxAngle, t.maxAngle);
  return -(s.mode === 'auto50' ? AUTO50_SHARE : 1) * passiveRatio(t, speed) * front * t.maxAngle;
}

/** One physics tick: lockout hysteresis on |speed|, then the hydraulic slew toward the target. */
export function rearSteerStep(t: RearSteerTuning, s: RearSteerState, frontInner01: number, speed: number, dt: number): RearSteerState {
  const v = Math.abs(speed);
  const locked = s.mode !== 'lock' && (s.locked ? v >= t.unlock : v >= t.lockout);
  const next = { mode: s.mode, locked, angle: s.angle };
  const target = rearSteerTarget(t, next, frontInner01, v);
  next.angle = s.angle + clamp(target - s.angle, -t.rate * dt, t.rate * dt);
  return next;
}

/** The next mode of the player's cycle AUTO → AUTO 50% → LOCK → SIDEWAYS → AUTO. */
export const nextRearMode = (mode: RearSteerMode): RearSteerMode =>
  REAR_STEER_CYCLE[(REAR_STEER_CYCLE.indexOf(mode) + 1) % REAR_STEER_CYCLE.length];

/** Effective wheelbase of the passive 4WS car for a bicycle-model follower (SPEC §3.2): L / (1 + r(v)·tanθr/tanθf). */
export function effectiveWheelbase(t: RearSteerTuning, wheelbase: number, frontLock: number, speed: number): number {
  return wheelbase / (1 + passiveRatio(t, speed) * Math.tan(t.maxAngle) / Math.tan(frontLock));
}
