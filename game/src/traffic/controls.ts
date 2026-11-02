import { hash01, mod } from './hash';
import type { Lane } from './tileNetwork';

/** Finnish signal sequence: green → amber → all red → red+amber → green. Two opposing approach groups.
 * Controller existence comes from roads.json; timings are a deterministic fallback, not measured Turku plans. */
export function signalState(lane: Lane, t: number): 'green' | 'amber' | 'red' | 'red-amber' {
  if (lane.signal === null) return 'green';
  const p = lane.points, n = p.length;
  const dx = p[n - 2] - p[n - 4], dz = p[n - 1] - p[n - 3];
  const group = Math.abs(dx) >= Math.abs(dz) ? 0 : 1;
  const phase = mod(t + hash01('signal-clock', lane.signal) * 60 - group * 30, 60);
  // The last six seconds serve left-turn arrows after through admissions stop and the box drains.
  const greenEnd = (lane.connector?.turn ?? 0) < -0.35 ? 24 : 18;
  return phase < greenEnd ? 'green' : phase < greenEnd + 3 ? 'amber' : phase < 59 ? 'red' : 'red-amber';
}

export function protectedLeft(lane: Lane, t: number) {
  if (!lane.signal || !lane.connector || lane.connector.turn >= -0.35) return false;
  const p = lane.points, n = p.length, h = [p[n - 2] - p[n - 4], p[n - 1] - p[n - 3]], group = Math.abs(h[0]) >= Math.abs(h[1]) ? 0 : 1;
  const phase = mod(t + hash01('signal-clock', lane.signal) * 60 - group * 30, 60);
  return phase >= 18 && phase < 24;
}

