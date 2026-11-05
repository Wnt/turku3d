import { hash01, mod } from './hash';
import type { Lane } from './tileNetwork';

/** Finnish signal sequence: green → amber → all red → red+amber → green over a 60 s cycle. Two opposing approach
 * groups. Controller existence comes from roads.json; timings are a deterministic fallback, not measured Turku plans. */
const CYCLE = 60;

/** The lane's signal clock at t (s, unwrapped: its cycle phase is clock mod CYCLE). */
function clock(lane: Lane, signal: string, t: number) {
  const p = lane.points, n = p.length;
  const dx = p[n - 2] - p[n - 4], dz = p[n - 1] - p[n - 3];
  const group = Math.abs(dx) >= Math.abs(dz) ? 0 : 1;
  return t + hash01('signal-clock', signal) * CYCLE - group * CYCLE / 2;
}

/** End of the lane's green within the cycle (s). The last six seconds serve left-turn arrows after through admissions
 *  stop and the box drains. */
const greenEnd = (lane: Lane) => (lane.connector?.turn ?? 0) < -0.35 ? 24 : 18;

export function signalState(lane: Lane, t: number): 'green' | 'amber' | 'red' | 'red-amber' {
  if (lane.signal === null) return 'green';
  const phase = mod(clock(lane, lane.signal, t), CYCLE), end = greenEnd(lane);
  return phase < end ? 'green' : phase < end + 3 ? 'amber' : phase < 59 ? 'red' : 'red-amber';
}

export function protectedLeft(lane: Lane, t: number) {
  if (!lane.signal || !lane.connector || lane.connector.turn >= -0.35) return false;
  const phase = mod(clock(lane, lane.signal, t), CYCLE);
  return phase >= 18 && phase < 24;
}

/** Seconds of green the lane had in the `span` seconds up to t (all of them without a signal). */
export function greenWithin(lane: Lane, t: number, span: number) {
  if (lane.signal === null || span <= 0) return Math.max(0, span);
  const end = greenEnd(lane), at = clock(lane, lane.signal, t);
  const green = (u: number) => Math.floor(u / CYCLE) * end + Math.min(mod(u, CYCLE), end);
  return green(at) - green(at - span);
}
