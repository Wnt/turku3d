/** AudioParam automation that keeps each param's event list bounded.
 *
 *  Chrome keeps a scheduled event until the audio thread renders past it, and each insertion scans the param's list.
 *  While the context is suspended (no user gesture yet; a gamepad press is not one) currentTime stands still, so a
 *  per-frame setTargetAtTime appends one more event to every param each frame and the cost grows without bound:
 *  Jonni's 2026-11-05 city.lab drive spent 8.0 s of a 60 s profile (13.5 %) in setTargetAtTime at 25-37 FPS.
 *  glide() cancels whatever is still scheduled from `time` on before it schedules the new target (a target event
 *  starts from the param's current computed value, so the curve stays continuous), and skips a target within
 *  `epsilon` of the last one it scheduled. Per-frame callers also cap their rate at MIX_HZ. */

/** The continuous mix (listener, positional loops, traffic and engine voices) updates at most this often. */
export const MIX_HZ = 30;

const targets = new WeakMap<AudioParam, number>();

/** Glides `param` towards `value` from `time` with `timeConstant`; returns false when the target was unchanged. */
export function glide(param: AudioParam, value: number, time: number, timeConstant: number, epsilon = 1e-3): boolean {
  const last = targets.get(param);
  if (last !== undefined && Math.abs(value - last) <= epsilon) return false;
  targets.set(param, value);
  param.cancelScheduledValues?.(time);
  param.setTargetAtTime(value, time, timeConstant);
  return true;
}

/** Positions (m) move in steps of at least 2 cm. */
export const POSITION_EPSILON = 0.02;

/** A cutoff frequency changes by at least half a percent. */
export const frequencyEpsilon = (hz: number) => Math.max(1, Math.abs(hz) * 0.005);
