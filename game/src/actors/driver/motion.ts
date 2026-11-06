/** A seated driver's motion channels, derived on every client from what it already draws of a vehicle: its world
 *  velocity and axes (the drawn pose), the front wheels' share of the steering lock, the horn and, for a locally
 *  simulated car, the physics impact event. Remote players' drivers therefore move like the sender's: no new synced
 *  field. Values: ~/turku3d/research/driver-animation/study.md "Turku3D driver rig targets" (lean, bounce and crash
 *  Wreckfest 2 measured; head look, reverse look and horn GTA V). Angles in radians, distances in metres. */

export interface Vec { x: number; y: number; z: number }
export interface MotionSample {
  /** Seconds (any monotonic clock). */
  t: number;
  /** World velocity (m/s). */
  v: Vec;
  /** The vehicle's world unit axes: forward, the driver's left, up. */
  fwd: Vec; left: Vec; up: Vec;
  /** Front road-wheel angle as a fraction of the lock (-1..1, + left). */
  steer: number;
  /** The horn sounds. */
  horn?: boolean;
  /** Physics impact event id of a locally simulated vehicle (VehicleState.impact.id). */
  impact?: number;
}
export interface MotionPose {
  /** Spine pitch (+ forward) and roll (+ toward the driver's right), summed over the three spine bones. */
  pitch: number; roll: number;
  /** Seat bounce: hips offset along the vehicle's up axis (m). */
  bump: number;
  /** Head yaw (+ left) and pitch (+ chin down) on top of the spine. */
  headYaw: number; headPitch: number;
  /** Reverse look over the passenger-side shoulder, 0..1. */
  reverse: number;
  /** The right hand on the hub for the horn, 0..1. */
  horn: number;
  /** Filtered body-frame g (diagnostics, tests). */
  gLon: number; gLat: number;
}

const G = 9.81, DEG = Math.PI / 180;
/** Acceleration window and the lean's response delay (study §5: delay 20–50 ms). */
const WINDOW = 0.05, DELAY = 0.05;
export const LEAN = { pitchPerG: 5 * DEG, pitchTau: 0.18, pitchMax: 8 * DEG, rollPerG: 2 * DEG, rollTau: 0.2, rollMax: 4 * DEG };
/** Seat spring: 3 Hz, damping ratio 0.6; dips up to 4 cm, a weightless lift up to 1.5 cm; head nods with the dip. */
export const BUMP = { hz: 3, zeta: 0.6, down: 0.04, up: 0.015, nod: 10 * DEG };
/** Crash whiplash per km/h of velocity change: head travel and pitch; attack, hold and first-order release. A crash
 *  is a change of at least `trigger` (m/s) within `span` (s); a local impact event lowers it to `eventTrigger`. */
export const WHIP = { travel: 0.0035, pitch: 0.6 * DEG, pitchMax: 30 * DEG, travelMax: 0.2, attack: 0.05, hold: 0.1, release: 0.15,
  span: 0.12, trigger: 2.5, eventTrigger: 1.2, refractory: 0.35, torso: 0.65 };
/** Head look into turns (+ left, GTA): 42° at full lock, even at rest, plus 0.05 s of yaw rate for slides; ±45°,
 *  τ 0.2 s. Reverse look: head 70° and upper spine 20° to the right, entered after 0.3 s below -0.8 m/s over 0.4 s,
 *  left over 0.4 s. Horn: the right hand to the hub over 0.15 s and back. */
export const LOOK = { lock: 42 * DEG, rate: 0.05, max: 45 * DEG, tau: 0.2, back: 70 * DEG, backSpine: 20 * DEG,
  reverseSpeed: -0.8, reverseAfter: 0.3, enter: 0.4, leave: 0.4, horn: 0.15 };

const dot = (a: Vec, b: Vec) => a.x * b.x + a.y * b.y + a.z * b.z;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const follow = (y: number, x: number, dt: number, tau: number) => y + (x - y) * (1 - Math.exp(-dt / tau));
const HISTORY = 0.4;

interface Entry { t: number; v: Vec; fwd: Vec }

export class DriverMotion {
  private history: Entry[] = [];
  private lean = { pitch: 0, roll: 0, gLon: 0, gLat: 0 };
  private spring = { x: 0, v: 0 };
  private look = 0;
  private back = { value: 0, below: 0 };
  private horn = 0;
  /** The crash being shown: its peak velocity change (body frame, km/h: forward and left components) and age. */
  private whip = { lon: 0, lat: 0, age: Infinity };
  private impact: number | undefined;
  readonly pose: MotionPose = { pitch: 0, roll: 0, bump: 0, headYaw: 0, headPitch: 0, reverse: 0, horn: 0, gLon: 0, gLat: 0 };

  /** Forget the past (a teleport, a new vehicle): no acceleration across the gap. */
  reset() {
    this.history = []; this.lean = { pitch: 0, roll: 0, gLon: 0, gLat: 0 }; this.spring = { x: 0, v: 0 }; this.look = 0;
    this.back = { value: 0, below: 0 }; this.horn = 0; this.whip = { lon: 0, lat: 0, age: Infinity }; this.impact = undefined;
  }

  /** The velocity at time `t`, interpolated (the oldest sample's if the history is shorter). */
  private velocityAt(t: number): Vec | null {
    const h = this.history;
    if (!h.length) return null;
    if (t <= h[0].t) return h[0].v;
    for (let i = h.length - 1; i > 0; i--) {
      const a = h[i - 1], b = h[i];
      if (t >= a.t) {
        const f = b.t > a.t ? (t - a.t) / (b.t - a.t) : 1;
        return { x: a.v.x + (b.v.x - a.v.x) * f, y: a.v.y + (b.v.y - a.v.y) * f, z: a.v.z + (b.v.z - a.v.z) * f };
      }
    }
    return h[h.length - 1].v;
  }

  update(s: MotionSample): MotionPose {
    const last = this.history.at(-1);
    const dt = last ? s.t - last.t : 0;
    if (last && (dt < 0 || dt > 0.5)) this.reset();
    if (last && dt === 0) return this.pose;
    const prevFwd = this.history.at(-1)?.fwd;
    this.history.push({ t: s.t, v: { ...s.v }, fwd: { ...s.fwd } });
    while (this.history.length > 2 && this.history[1].t < s.t - HISTORY) this.history.shift();
    const step = Math.min(dt, 0.1);
    const span = s.t - this.history[0].t;

    // Body-frame acceleration over WINDOW, DELAY ago (forward, left) for the lean; the seat spring takes the newest
    // vertical one (a landing is a single-frame spike).
    let aFwd = 0, aLeft = 0, nowUp = 0;
    if (span >= WINDOW + DELAY) {
      const v1 = this.velocityAt(s.t - DELAY)!, v0 = this.velocityAt(s.t - DELAY - WINDOW)!;
      const a = { x: (v1.x - v0.x) / WINDOW, y: (v1.y - v0.y) / WINDOW, z: (v1.z - v0.z) / WINDOW };
      aFwd = dot(a, s.fwd); aLeft = dot(a, s.left);
    }
    if (span >= WINDOW) {
      const v1 = s.v, v0 = this.velocityAt(s.t - WINDOW)!;
      nowUp = dot({ x: (v1.x - v0.x) / WINDOW, y: (v1.y - v0.y) / WINDOW, z: (v1.z - v0.z) / WINDOW }, s.up);
    }
    const gLon = aFwd / G, gLat = -aLeft / G;
    if (step > 0) {
      this.lean.gLon = follow(this.lean.gLon, gLon, step, LEAN.pitchTau);
      this.lean.gLat = follow(this.lean.gLat, gLat, step, LEAN.rollTau);
      // Braking (negative g) pitches forward; a turn's lateral g (toward the inside) rolls outward.
      this.lean.pitch = clamp(-LEAN.pitchPerG * this.lean.gLon, -LEAN.pitchMax, LEAN.pitchMax);
      this.lean.roll = clamp(-LEAN.rollPerG * this.lean.gLat, -LEAN.rollMax, LEAN.rollMax);
      this.bounce(step, nowUp);
    }

    // Crash whiplash: the body keeps the pre-impact velocity; the body-frame change over the crash span sets it.
    this.whipUpdate(s, step);

    // Head look: the steer fraction, plus the yaw rate (rad/s, + left) from the drawn forward axis.
    let rate = 0;
    if (prevFwd && dt > 0) {
      const cx = prevFwd.y * s.fwd.z - prevFwd.z * s.fwd.y, cy = prevFwd.z * s.fwd.x - prevFwd.x * s.fwd.z, cz = prevFwd.x * s.fwd.y - prevFwd.y * s.fwd.x;
      rate = Math.atan2(cx * s.up.x + cy * s.up.y + cz * s.up.z, dot(prevFwd, s.fwd)) / dt;
    }
    const lookTarget = clamp(LOOK.lock * s.steer + LOOK.rate * rate, -LOOK.max, LOOK.max);
    if (step > 0) this.look = follow(this.look, lookTarget, step, LOOK.tau);
    const forwardSpeed = dot(s.v, s.fwd);
    this.back.below = forwardSpeed < LOOK.reverseSpeed ? this.back.below + step : 0;
    const backTarget = this.back.below >= LOOK.reverseAfter ? 1 : forwardSpeed < 0.3 * LOOK.reverseSpeed && this.back.value > 0 ? this.back.value : 0;
    this.back.value = backTarget > this.back.value ? Math.min(backTarget, this.back.value + step / LOOK.enter)
      : Math.max(backTarget, this.back.value - step / LOOK.leave);

    const envelope = this.envelope();
    const whipLon = clamp(WHIP.travel * this.whip.lon, -WHIP.travelMax, WHIP.travelMax) * envelope;
    const whipLat = clamp(WHIP.travel * this.whip.lat, -WHIP.travelMax, WHIP.travelMax) * envelope;
    const whipPitch = clamp(WHIP.pitch * this.whip.lon, -WHIP.pitchMax / 2, WHIP.pitchMax) * envelope;
    const p = this.pose;
    p.pitch = this.lean.pitch + Math.asin(clamp(whipLon / WHIP.torso, -1, 1));
    // A side crash throws the torso toward the side the car was pushed away from (positive roll leans right).
    p.roll = this.lean.roll - Math.asin(clamp(whipLat / WHIP.torso, -1, 1));
    p.bump = this.spring.x;
    const nod = clamp(-this.spring.x / BUMP.down, 0, 1) * BUMP.nod;
    p.headPitch = whipPitch + nod;
    const reverse = smooth(this.back.value);
    p.reverse = reverse;
    this.horn = s.horn ? Math.min(1, this.horn + step / LOOK.horn) : Math.max(0, this.horn - step / LOOK.horn);
    p.horn = smooth(this.horn);
    p.headYaw = this.look * (1 - reverse);
    p.gLon = this.lean.gLon; p.gLat = this.lean.gLat;
    return p;
  }

  /** Seat spring in the vehicle frame: the hips lag the seat's vertical acceleration (a landing dips, free fall lifts). */
  private bounce(dt: number, aUp: number) {
    const w = 2 * Math.PI * BUMP.hz, n = Math.max(1, Math.ceil(dt / (1 / 120)));
    const h = dt / n;
    for (let i = 0; i < n; i++) {
      this.spring.v += (-w * w * this.spring.x - 2 * BUMP.zeta * w * this.spring.v - aUp) * h;
      this.spring.x += this.spring.v * h;
      if (this.spring.x < -BUMP.down) { this.spring.x = -BUMP.down; this.spring.v = Math.max(0, this.spring.v); }
      if (this.spring.x > BUMP.up) { this.spring.x = BUMP.up; this.spring.v = Math.min(0, this.spring.v); }
    }
  }

  private whipUpdate(s: MotionSample, dt: number) {
    const w = this.whip;
    w.age += dt;
    const event = s.impact !== undefined && this.impact !== undefined && s.impact !== this.impact;
    this.impact = s.impact;
    const before = this.velocityAt(s.t - WHIP.span);
    if (!before) return;
    const dv = { x: before.x - s.v.x, y: before.y - s.v.y, z: before.z - s.v.z };
    const lon = dot(dv, s.fwd), lat = dot(dv, s.left), size = Math.hypot(lon, lat);
    if (w.age < WHIP.attack + WHIP.hold) {
      // Still rising or holding: the crash keeps summing over its span, so follow its peak.
      if (size * 3.6 > Math.hypot(w.lon, w.lat)) { w.lon = lon * 3.6; w.lat = lat * 3.6; }
    } else if (size >= (event ? WHIP.eventTrigger : WHIP.trigger) && w.age >= WHIP.refractory) {
      w.lon = lon * 3.6; w.lat = lat * 3.6; w.age = 0;
    }
  }

  /** Attack (linear), hold, then first-order release. */
  private envelope() {
    const a = this.whip.age;
    if (!Number.isFinite(a)) return 0;
    if (a < WHIP.attack) return a / WHIP.attack;
    if (a < WHIP.attack + WHIP.hold) return 1;
    return Math.exp(-(a - WHIP.attack - WHIP.hold) / WHIP.release);
  }
}

const smooth = (x: number) => x * x * (3 - 2 * x);
