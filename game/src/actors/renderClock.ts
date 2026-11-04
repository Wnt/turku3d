import type { V3 } from '../core/origin';
import type { Quat } from '../physics/protocol';

/** Render clock for a stream of timestamped states (physics ticks, network poses): maps local time to the source
 *  time to draw. The local physics car and multiplayer remotes use the same rule, and the camera follows what it
 *  draws, so camera and car share one clock.
 *
 *  - `offset`: the earliest arrival (local s) minus source time over the last WINDOW seconds, i.e. the fastest
 *    delivery seen lately.
 *  - `delay`: that delivery's jitter (the JITTER_QUANTILE of later arrivals) plus `minDelay`, capped at `maxDelay`:
 *    late states within it are still interpolated; rarer, later ones are extrapolated.
 *  - The drawn source time never jumps while running: it runs 0.9-1.1x real time towards `now - offset - delay`.
 *    It only resyncs on a new epoch (pause, rejoin, a change of over RESYNC seconds). */
const WINDOW = 0.6;
const JITTER_QUANTILE = 0.9;
const RESYNC = 0.25;
const SLEW = 0.1;
const GAIN = 3;

export class SourceClock {
  private arrivals: { at: number; offset: number }[] = [];
  private source: number | null = null;
  private last = 0;
  delay = 0;
  constructor(private readonly minDelay: number, private readonly maxDelay: number) {}
  reset() { this.arrivals = []; this.source = null; }
  /** A state of `source` time (s) became available at local time `arrival` (s). */
  observe(source: number, arrival: number) {
    const offset = arrival - source, min = this.minOffset();
    // A pause, a rejoin or a clock change: earlier arrivals say nothing about the new epoch.
    if (min !== null && Math.abs(offset - min) > RESYNC) { this.arrivals = []; this.source = null; }
    this.arrivals.push({ at: arrival, offset });
    while (this.arrivals.length > 1 && this.arrivals[0].at < arrival - WINDOW) this.arrivals.shift();
  }
  private minOffset() {
    let min: number | null = null;
    for (const a of this.arrivals) if (min === null || a.offset < min) min = a.offset;
    return min;
  }
  /** The source time to draw at local time `now` (s); null before the first state. */
  at(now: number): number | null {
    const min = this.minOffset();
    if (min === null) return null;
    const late = this.arrivals.map(a => a.offset - min).sort((a, b) => a - b);
    const jitter = late[Math.min(late.length - 1, Math.floor(late.length * JITTER_QUANTILE))];
    this.delay = Math.min(this.maxDelay, this.minDelay + jitter);
    const target = now - min - this.delay;
    if (this.source === null || Math.abs(target - this.source) > RESYNC || now < this.last) this.source = target;
    else {
      const ahead = this.source + (now - this.last);
      const rate = 1 + Math.max(-SLEW, Math.min(SLEW, (target - ahead) * GAIN));
      this.source += (now - this.last) * rate;
    }
    this.last = now;
    return this.source;
  }
}

const multiply = (a: Quat, b: Quat): Quat => ({
  x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y, y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
  z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w, w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z });
export const quatMultiply = multiply;
export const quatConjugate = (q: Quat): Quat => ({ x: -q.x, y: -q.y, z: -q.z, w: q.w });
/** Rotation vector (axis * angle) of `q`, shortest arc. */
export function quatLog(q: Quat): V3 {
  const s = q.w < 0 ? -1 : 1, w = Math.min(1, q.w * s), half = Math.acos(w), sin = Math.sin(half);
  if (sin < 1e-9) return { x: 0, y: 0, z: 0 };
  const k = 2 * half / sin * s;
  return { x: q.x * k, y: q.y * k, z: q.z * k };
}
export function quatExp(v: V3): Quat {
  const angle = Math.hypot(v.x, v.y, v.z);
  if (angle < 1e-12) return { x: 0, y: 0, z: 0, w: 1 };
  const k = Math.sin(angle / 2) / angle;
  return { x: v.x * k, y: v.y * k, z: v.z * k, w: Math.cos(angle / 2) };
}

/** Teleports (resets, seat changes) are never smoothed. */
export const MAX_CORRECTION = 3;

/** Critically damped error between the drawn pose and the authoritative one. A late state replaces an
 *  extrapolated pose; the difference is added here and decays without overshoot instead of snapping. The rate is
 *  chosen so the correction's peak speed stays under half the actor's speed: it never pulls a moving car backwards. */
export class PoseCorrection {
  private e = { x: 0, y: 0, z: 0 };
  private v = { x: 0, y: 0, z: 0 };
  private r = { x: 0, y: 0, z: 0 };
  private w = { x: 0, y: 0, z: 0 };
  private omega = 10;
  private at: number | null = null;
  reset() { this.e = { x: 0, y: 0, z: 0 }; this.v = { x: 0, y: 0, z: 0 }; this.r = { x: 0, y: 0, z: 0 }; this.w = { x: 0, y: 0, z: 0 }; this.at = null; }
  get size() { return Math.hypot(this.e.x, this.e.y, this.e.z); }
  /** Adds a jump of `dp` (drawn minus authoritative position) and `dr` (rotation vector of drawn * authoritative^-1). */
  add(dp: V3, dr: V3 | null, speed: number) {
    const e = { x: this.e.x + dp.x, y: this.e.y + dp.y, z: this.e.z + dp.z };
    if (Math.hypot(e.x, e.y, e.z) > MAX_CORRECTION) { this.reset(); return; }
    this.e = e;
    if (dr) this.r = { x: this.r.x + dr.x, y: this.r.y + dr.y, z: this.r.z + dr.z };
    // Peak speed of a critically damped decay from rest is omega * e / E.
    const size = Math.hypot(e.x, e.y, e.z);
    this.omega = Math.max(4, Math.min(14, size > 1e-6 ? 0.5 * Math.max(speed, 1) * Math.E / size : 14));
  }
  /** Advances to local time `now` (s) and returns the position offset and rotation to apply on the authoritative pose. */
  apply(now: number): { dp: V3; dr: Quat } | null {
    const h = this.at === null ? 0 : Math.max(0, now - this.at);
    this.at = now;
    const o = this.omega, k = Math.exp(-o * h);
    for (const [x, v] of [[this.e, this.v], [this.r, this.w]] as const) for (const c of ['x', 'y', 'z'] as const) {
      const b = v[c] + o * x[c];
      x[c] = (x[c] + b * h) * k; v[c] = (v[c] - o * b * h) * k;
    }
    if (this.size < 1e-4 && Math.hypot(this.r.x, this.r.y, this.r.z) < 1e-5 && Math.hypot(this.v.x, this.v.y, this.v.z) < 1e-3) {
      this.reset(); this.at = now; return null;
    }
    return { dp: { ...this.e }, dr: quatExp(this.r) };
  }
}
