// The `drv` pose field of a driven 4WS truck (SPEC §5): what a remote engine voice and HUD need beyond the wheels,
// as four small integers. `server/pose.js` validates the same ranges.
import type { VehicleState } from '../physics/protocol';
import { REAR_STEER_MODES, type RearSteerMode } from '../vehicle/rearSteer';

/** [throttle percent 0..100, rpm / 10 0..800, gear −1..3 (−1 reverse, 0 neutral), rear mode index 0..3]. The mode
 *  index is REAR_STEER_MODES order: 0 AUTO, 1 SIDEWAYS (crab), 2 AUTO 50% (MANUAL before 2026-11-06), 3 LOCK. Remote
 *  rear wheels follow the synced wheel `steer`, not this index, so every mode looks right to every client. */
export type Drv = [throttlePct: number, rpm10: number, gear: number, rearMode: number];
export interface DrvValues { throttle: number; rpm: number; gear: number; rearMode: RearSteerMode }

const LIMITS: readonly (readonly [number, number])[] = [[0, 100], [0, 800], [-1, 3], [0, 3]];
const clampInt = (v: number, [lo, hi]: readonly [number, number]) => Math.min(hi, Math.max(lo, Math.round(Number.isFinite(v) ? v : 0)));

export function encodeDrv(v: DrvValues): Drv {
  const mode = Math.max(0, REAR_STEER_MODES.indexOf(v.rearMode));
  return [clampInt(v.throttle * 100, LIMITS[0]), clampInt(v.rpm / 10, LIMITS[1]), clampInt(v.gear, LIMITS[2]), clampInt(mode, LIMITS[3])];
}

/** A well-formed `drv`: four integers within their ranges. */
export const validDrv = (d: unknown): d is Drv => Array.isArray(d) && d.length === 4
  && d.every((n, i) => Number.isInteger(n) && n >= LIMITS[i][0] && n <= LIMITS[i][1]);

export function decodeDrv(d: unknown): DrvValues | null {
  if (!validDrv(d)) return null;
  return { throttle: d[0] / 100, rpm: d[1] * 10, gear: d[2], rearMode: REAR_STEER_MODES[d[3]] };
}

/** The `drv` of a driven vehicle state with the sound drivetrain's rpm and gear; undefined without rear steer. */
export function drvOf(state: VehicleState | null | undefined, rpm: number, gear: number): Drv | undefined {
  if (!state?.rearMode) return undefined;
  return encodeDrv({ throttle: state.throttleApplied ?? 0, rpm, gear, rearMode: state.rearMode });
}
