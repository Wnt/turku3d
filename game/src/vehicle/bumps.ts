// Wheel bumps for haptics: what a tyre feels on any surface, independent of collision materials. Lidar kerbs in the
// real tiles are 10–20 cm slopes over about 0.6 m that Rapier's ray wheels roll up smoothly, so the suspension alone
// barely reacts at walking pace. Two channels therefore report:
//  - step: the change of slope of a wheel's ground path, h(s) - 2 h(s - W) + h(s - 2W) over travelled distance s.
//    A kerb of height h reads about h whatever its ramp width up to W; a steady hill reads 0.
//  - jolt: suspension compression speed (m/s): humps and potholes at speed, landings after a jump.
import type { BumpState } from '../physics/protocol';

/** Travel (m) over which a step is measured. */
export const STEP_WINDOW = 0.6;
/** Smallest step (m) and jolt (m/s) reported. input/haptics.ts decides how strongly each is felt. */
const STEP_MIN = 0.05;
const JOLT_MIN = 0.5;
const SAMPLE = 0.05;
/** A step's measure swings to the opposite sign one window later; that lobe is not a second step. */
const LOBE = 3 * STEP_WINDOW;

interface WheelPath {
  s: number[]; y: number[]; length: number | null;
  /** Sign of the step burst this wheel is in (0: none), where it ends, and whether it has been reported. */
  burst: number; until: number; spent: boolean;
}
interface WheelSample { length: number; ground: number | null }
interface Peak { wheel: number; value: number }

export class BumpDetector {
  private travel = 0;
  private wheels: WheelPath[] = [];
  private id = 0;
  private stepPeak: Peak | null = null;
  private joltPeak: Peak | null = null;
  latest: BumpState | null = null;

  reset() { this.wheels = []; this.stepPeak = this.joltPeak = null; this.latest = null; this.travel = 0; }

  /** One fixed step: horizontal speed (m/s) and per wheel its suspension length and ground contact height. */
  update(dt: number, speed: number, samples: readonly WheelSample[]) {
    this.travel += Math.abs(speed) * dt;
    let step: Peak | null = null, jolt: Peak | null = null;
    for (let i = 0; i < samples.length; i++) {
      const sample = samples[i];
      const w = this.wheels[i] ??= { s: [], y: [], length: null, burst: 0, until: 0, spent: false };
      const compression = w.length === null ? 0 : (w.length - sample.length) / dt;
      w.length = sample.length;
      if (compression >= JOLT_MIN && (!jolt || compression > jolt.value)) jolt = { wheel: i, value: compression };
      // A wheel in the air starts a new path when it lands; the landing itself is a jolt.
      if (sample.ground === null) { w.s.length = 0; w.y.length = 0; }
      const height = this.burst(w, sample.ground === null ? 0 : this.measure(w, sample.ground));
      if (height && (!step || Math.abs(height) > Math.abs(step.value))) step = { wheel: i, value: height };
    }
    // Each channel holds its peak while it grows and reports it on the first tick it does not.
    const grows = (peak: Peak | null, now: Peak | null) => !!now && (!peak || Math.abs(now.value) > Math.abs(peak.value));
    let height = 0, compression = 0, wheel = -1;
    if (grows(this.stepPeak, step)) this.stepPeak = step;
    else if (this.stepPeak) {
      ({ wheel, value: height } = this.stepPeak);
      this.stepPeak = null;
      for (const w of this.wheels) if (w.burst) w.spent = true;
    }
    if (grows(this.joltPeak, jolt)) this.joltPeak = jolt;
    else if (this.joltPeak) {
      compression = this.joltPeak.value;
      if (wheel < 0) wheel = this.joltPeak.wheel;
      this.joltPeak = null;
    }
    if (wheel >= 0) this.latest = { id: ++this.id, wheel, height, jolt: compression, speed: Math.abs(speed) };
  }

  /** The step this wheel may report now: within a burst only its own sign counts, and only until reported. */
  private burst(w: WheelPath, raw: number) {
    const sign = Math.abs(raw) >= STEP_MIN / 2 ? Math.sign(raw) : 0;
    if (this.travel >= w.until && (sign === 0 || sign !== w.burst)) w.burst = 0;
    if (sign && !w.burst) { w.burst = sign; w.until = this.travel + LOBE; w.spent = false; }
    return sign === w.burst && !w.spent && Math.abs(raw) >= STEP_MIN ? raw : 0;
  }

  private measure(w: WheelPath, ground: number) {
    const s = this.travel;
    if (!w.s.length || s - w.s[w.s.length - 1] >= SAMPLE) { w.s.push(s); w.y.push(ground); }
    while (w.s.length > 2 && w.s[1] <= s - 2 * STEP_WINDOW) { w.s.shift(); w.y.shift(); }
    if (w.s[0] > s - 2 * STEP_WINDOW) return 0;
    return ground - 2 * heightAt(w, s - STEP_WINDOW) + heightAt(w, s - 2 * STEP_WINDOW);
  }
}

function heightAt(w: WheelPath, s: number) {
  let k = 1;
  while (k < w.s.length - 1 && w.s[k] < s) k++;
  const s0 = w.s[k - 1], s1 = w.s[k], t = s1 > s0 ? Math.min(1, Math.max(0, (s - s0) / (s1 - s0))) : 0;
  return w.y[k - 1] + (w.y[k] - w.y[k - 1]) * t;
}
