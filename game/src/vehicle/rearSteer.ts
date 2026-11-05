// Rear-wheel steering of the 4400 car (SPEC §3.2). Pure and Node-importable: the physics worker steps it per vehicle,
// and the tests and the autopilot read the same ratio.

/** Wire order 0..3 (MP `drv[3]`). `auto` is the passive counter-phase steer, `crab` in-phase, `manual` a held rocker,
 *  `lock` centred. */
export type RearSteerMode = 'auto' | 'crab' | 'manual' | 'lock';
export const REAR_STEER_MODES: readonly RearSteerMode[] = ['auto', 'crab', 'manual', 'lock'];

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
  /** Rocker integration rate in manual mode (rad/s). */
  manualRate: number;
  /** Front inner-wheel lock (rad): CRAB copies the front angle itself, up to `maxAngle`. Absent: `maxAngle`. */
  frontLock?: number;
}

/** `angle`: the rear inner-wheel angle (rad, + left, the chassis steer sign); `manual`: the held rocker angle. */
export interface RearSteerState { mode: RearSteerMode; angle: number; locked: boolean; manual: number }

export const rearSteerIdle = (mode: RearSteerMode = 'auto'): RearSteerState => ({ mode, angle: 0, locked: false, manual: 0 });

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
  if (s.mode === 'auto') return -passiveRatio(t, speed) * front * t.maxAngle;
  if (s.mode === 'crab') return clamp(front * (t.frontLock ?? t.maxAngle), -t.maxAngle, t.maxAngle);
  return s.manual;
}

/** One physics tick: lockout hysteresis on |speed|, the manual rocker, then the hydraulic slew toward the target. */
export function rearSteerStep(t: RearSteerTuning, s: RearSteerState, frontInner01: number, speed: number,
  rocker: number, dt: number): RearSteerState {
  const v = Math.abs(speed);
  const locked = s.mode !== 'lock' && (s.locked ? v >= t.unlock : v >= t.lockout);
  let manual = s.mode === 'manual' && !locked
    ? clamp(s.manual + clamp(rocker, -1, 1) * t.manualRate * dt, -t.maxAngle, t.maxAngle) : 0;
  if (!Number.isFinite(manual)) manual = 0;
  const next = { mode: s.mode, locked, manual, angle: s.angle };
  const target = rearSteerTarget(t, next, frontInner01, v);
  next.angle = s.angle + clamp(target - s.angle, -t.rate * dt, t.rate * dt);
  return next;
}

/** The next mode of the cycle AUTO → CRAB → MANUAL → LOCK → AUTO. */
export const nextRearMode = (mode: RearSteerMode): RearSteerMode =>
  REAR_STEER_MODES[(REAR_STEER_MODES.indexOf(mode) + 1) % REAR_STEER_MODES.length];

/** Effective wheelbase of the passive 4WS car for a bicycle-model follower (SPEC §3.2): L / (1 + r(v)·tanθr/tanθf). */
export function effectiveWheelbase(t: RearSteerTuning, wheelbase: number, frontLock: number, speed: number): number {
  return wheelbase / (1 + passiveRatio(t, speed) * Math.tan(t.maxAngle) / Math.tan(frontLock));
}
