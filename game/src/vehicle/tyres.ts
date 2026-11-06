// Slip-curve tyres for the drivable cars. Rapier's ray-cast vehicle keeps the suspension (spring, damper, contact
// point, load) and this layer replaces its tyre: Rapier solves every contact patch to zero side slip until a friction
// ellipse saturates, so a car had no slip angle, no understeer and no load sensitivity. Here each tyre's force comes
// from its slip angle, its load and how much of its grip braking or drive uses, calibrated on the Wreckfest 2 Bravion
// (~/turku3d/research/wreckfest2/physics-study.md, "Turku3D tuning targets"; vehicle/README.md "Tyres").
// Headless: plain vectors in and impulses out, so vehicle/vehicle.ts applies the result to its Rapier body.
import type { V3 } from '../core/origin.ts';

/** μ(s) = peak · (1 − e^(−s / slip)): rises with slip and saturates without falling off (the study's axle fit). */
export interface SlipCurve { readonly peak: number; readonly slip: number }

export interface TyreTuning {
  /** Lateral μ against slip angle (rad), front and rear axle, at the reference load. */
  readonly lateral: readonly [front: SlipCurve, rear: SlipCurve];
  /** Longitudinal μ: the peak a rolling tyre transmits in braking or drive, and the μ of a spinning wheel. */
  readonly longitudinal: { readonly peak: number; readonly sliding: number };
  /** Load sensitivity: μ × (Fz / (reference × static corner load))^−exponent, clamped to [min, max]. */
  readonly load: { readonly exponent: number; readonly reference: number; readonly min: number; readonly max: number };
  /** Share of the longitudinal peak the brakes use at most: an ideal anti-lock limit, so braking never locks a wheel. */
  readonly brakeShare: number;
  /** Traction control: the drive force at most this multiple of the tyre's peak (1: the wheels never spin).
   *  Absent: none, and drive beyond the peak spins the wheel at its sliding μ. */
  readonly traction?: number;
  /** μ of the rear wheels the handbrake locks, as a share of the sliding μ. */
  readonly handbrake: number;
  /** Height above the contact (m) where each axle's side force acts on the body (its roll centre). */
  readonly rollCentre: readonly [front: number, rear: number];
  /** Relaxation length (m): the side force follows a change of slip angle over this much rolled distance, and
   *  within `relaxationTime` (s) at walking pace, where a distance alone would leave the car sliding. */
  readonly relaxation: number;
  readonly relaxationTime: number;
}

/** Wreckfest 2 Bravion on tarmac (physics-study.md "Fits"): lateral axle fits 1.16 (1 − e^(−α/3.3°)) front and
 *  1.09 (1 − e^(−α/2.7°)) rear with no fall-off to 25°; per-wheel peak about 1.35 at 1.5 kN and 1.15-1.2 at 4-6 kN
 *  (exponent 0.12 about 1.75 × the static load, where the outer wheels run at the limit); longitudinal peak 1.35 on a
 *  braking front wheel at 1.46 × static load and 1.5 on the rear at 0.5 ×, drive 1.42 at 1.43 ×; 1.33-1.37 locked or
 *  spinning (97 % of the peak). */
export const TARMAC: TyreTuning = {
  lateral: [{ peak: 1.16, slip: 3.45 * Math.PI / 180 }, { peak: 1.09, slip: 2.7 * Math.PI / 180 }],
  longitudinal: { peak: 1.35, sliding: 1.31 },
  load: { exponent: 0.12, reference: 1.75, min: 0.85, max: 1.3 },
  brakeShare: 0.95,
  traction: 1,
  handbrake: 0.75,
  // At the patches: springs and anti-roll bars (vehicle/ride.ts) carry the whole lateral load transfer.
  rollCentre: [0, 0],
  relaxation: 1.0,
  relaxationTime: 0.1,
};

export const slipMu = (c: SlipCurve, slip: number) => c.peak * (1 - Math.exp(-Math.abs(slip) / c.slip));

/** Load-sensitivity factor of a tyre carrying `load` N on a car whose static corner load is `corner` N. */
export function loadFactor(t: TyreTuning, load: number, corner: number) {
  const l = t.load, ratio = Math.max(1e-3, load) / Math.max(1, l.reference * corner);
  return Math.min(l.max, Math.max(l.min, ratio ** -l.exponent));
}

/** One wheel in contact: world contact point and ground normal, the wheel's heading and axle projected on the
 *  ground plane (unit), its load, axle, drive force (N, + forward), brake force capacity (N) and surface grip. */
export interface TyrePatch {
  point: V3; normal: V3; forward: V3; side: V3;
  load: number; axle: 0 | 1;
  drive: number; brake: number;
  /** Locked by the handbrake: the patch slides with the full velocity. */
  locked: boolean;
  grip: number;
}

/** Rigid body velocity state in world space; `invInertia` is the world inverse inertia, row-major 3 × 3, and
 *  `gravity` the world gravity (m/s²) that the coming world step adds. */
export interface BodyState { lin: V3; ang: V3; com: V3; invMass: number; invInertia: readonly number[]; gravity: V3 }

/** Per-vehicle solver memory: each wheel's relaxed slip angle and its last impulses along the heading and the axle
 *  (N·s), which start the next step's iterations (warm start: a parked car on a slope converges to rest). */
export interface TyreState { readonly lag: Float64Array; readonly jx: Float64Array; readonly jy: Float64Array }
export const tyreState = (wheels: number): TyreState =>
  ({ lag: new Float64Array(wheels), jx: new Float64Array(wheels), jy: new Float64Array(wheels) });
export const resetTyres = (s: TyreState) => { s.lag.fill(0); s.jx.fill(0); s.jy.fill(0); };

/** Per-wheel result: forces (N) along the wheel's heading and axle, the slip angle (rad, + the patch moving to the
 *  wheel's right) and the longitudinal sliding speed estimate (m/s, tyre marks and smoke). */
export interface TyreResult { fx: number; fy: number; slip: number; slide: number }

const cross = (a: V3, b: V3): V3 => ({ x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x });
const dot = (a: V3, b: V3) => a.x * b.x + a.y * b.y + a.z * b.z;
const mul = (m: readonly number[], v: V3): V3 => ({
  x: m[0] * v.x + m[1] * v.y + m[2] * v.z, y: m[3] * v.x + m[4] * v.y + m[5] * v.z, z: m[6] * v.x + m[7] * v.y + m[8] * v.z,
});
const ITERATIONS = 6, WARM = 0.9;
/** Normalised slip angle (α / α0) at which a slip-limiting traction control or anti-lock leaves 71 % of the
 *  longitudinal peak: a tyre's force follows its slip direction, so at the slip angle of the side peak a wheel held
 *  near its peak slip ratio transmits about as much along as across, and at 25° little along. */
const COMBINED = 2;
/** Below this contact speed (m/s) a tyre holds like static friction: its side bound rises to the peak. */
const STATIC_SPEED = 1;

/** Solves the tyre impulses of one step and applies them to `body` (velocities change in place). Each tyre is a
 *  velocity constraint on its contact patch (no slip along the axle; along the heading only while braking) whose
 *  impulse is bounded by the slip curve's force at the step's starting slip: at road speed the bound always binds,
 *  so the side force is μ(α, Fz)·Fz; at walking pace the constraint itself binds and the tyre holds like static
 *  friction instead of oscillating. Projected Gauss-Seidel over the wheels. Returns the summed impulse and its
 *  angular impulse about the centre of mass, to apply to the rigid body, and writes per-wheel forces into `out`. */
export function solveTyres(body: BodyState, wheels: readonly (TyrePatch | null)[], t: TyreTuning, corner: number,
  dt: number, out: TyreResult[], state: TyreState): { impulse: V3; angular: V3 } {
  const lag = state.lag;
  const n = wheels.length;
  const impulse = { x: 0, y: 0, z: 0 }, angular = { x: 0, y: 0, z: 0 };
  // The world step adds gravity after these impulses: solving against it now holds a braked car still on a slope
  // instead of letting it creep a step's worth of gravity every step. (A local copy: the body gets only impulses.)
  body.lin.x += body.gravity.x * dt; body.lin.y += body.gravity.y * dt; body.lin.z += body.gravity.z * dt;
  const rows = wheels.map((w, i) => {
    out[i] = { fx: 0, fy: 0, slip: 0, slide: 0 };
    if (!w || w.load <= 0) { lag[i] = state.jx[i] = state.jy[i] = 0; return null; }
    const rx = { x: w.point.x - body.com.x, y: w.point.y - body.com.y, z: w.point.z - body.com.z };
    const h = t.rollCentre[w.axle];
    const ry = { x: rx.x + w.normal.x * h, y: rx.y + w.normal.y * h, z: rx.z + w.normal.z * h };
    const ax = cross(rx, w.forward), ay = cross(ry, w.side);
    const mx = 1 / (body.invMass + dot(ax, mul(body.invInertia, ax))), my = 1 / (body.invMass + dot(ay, mul(body.invInertia, ay)));
    const v = velocity(body, rx), vx = dot(v, w.forward), vy = dot(v, w.side), speed = Math.hypot(vx, vy);
    // The slip angle the side force follows: relaxed over `relaxation` metres of rolling (a fresh contact starts there).
    const alpha = Math.atan2(vy, Math.abs(vx));
    lag[i] += (alpha - lag[i]) * Math.min(1, Math.max(Math.abs(vx) / t.relaxation, 1 / t.relaxationTime) * dt);
    const scale = loadFactor(t, w.load, corner) * w.grip, still = Math.max(0, 1 - speed / STATIC_SPEED);
    const lat = t.lateral[w.axle], curve = slipMu(lat, lag[i]);
    const capX = t.longitudinal.peak * scale * w.load * dt;
    out[i].slip = alpha;
    if (w.locked) {
      const cap = t.handbrake * t.longitudinal.sliding * scale * w.load * dt;
      out[i].slide = speed;
      return { w, rx, ry, ax, ay, mx, my, locked: true, cap, jd: 0, lo: 0, hi: 0, capY: 0, jx: 0, jy: 0 };
    }
    // Longitudinal use of the peak: drive (traction control cuts it at its limit; without it, drive beyond the peak
    // spins the wheel at its sliding μ) and the brakes, which bound a constraint that stops the patch rolling: at
    // speed they pull their whole bound, at rest they hold.
    const limit = 1 / Math.hypot(1, lag[i] / lat.slip / COMBINED);
    const demand = Math.abs(w.drive * dt) / capX, spinning = t.traction === undefined && demand > 1;
    const drive = spinning ? t.longitudinal.sliding / t.longitudinal.peak : Math.min(demand, (t.traction ?? 1) * limit);
    const side = curve / lat.peak, moving = Math.abs(vx) > STATIC_SPEED;
    // Rear brakes give way to the rear tyres' side force (brake-force distribution with cornering brake control), so
    // braking in a turn cannot take the tail's grip away; the front brakes share their tyre's ellipse.
    const brake = Math.min(w.brake * dt / capX, t.brakeShare * (moving ? limit : 1) * (w.axle && moving ? Math.sqrt(Math.max(0, 1 - side * side)) : 1));
    // Friction ellipse: when the longitudinal use and the slip curve's side use together exceed it, both shrink in
    // proportion (the side force keeps a share at full throttle instead of vanishing).
    const long = drive + (moving ? brake : 0), over = Math.hypot(long, side);
    const fit = over > 1 ? 1 / over : 1;
    out[i].slide = spinning ? Math.abs(vx) * (1 - 1 / demand) + demand - 1 : moving ? 0.12 * long * long * Math.abs(vx) : 0;
    const jd = Math.sign(w.drive) * drive * fit * capX, jb = brake * fit * capX;
    return { w, rx, ry, ax, ay, mx, my, locked: false, cap: 0, jd, lo: -jb, hi: jb,
      capY: Math.max(curve * fit, lat.peak * still) * scale * w.load * dt, jx: 0, jy: 0 };
  });
  const apply = (r: NonNullable<(typeof rows)[number]>, dx: number, dy: number) => {
    const w = r.w;
    const J = { x: w.forward.x * dx + w.side.x * dy, y: w.forward.y * dx + w.side.y * dy, z: w.forward.z * dx + w.side.z * dy };
    const L = { x: r.ax.x * dx + r.ay.x * dy, y: r.ax.y * dx + r.ay.y * dy, z: r.ax.z * dx + r.ay.z * dy };
    body.lin.x += J.x * body.invMass; body.lin.y += J.y * body.invMass; body.lin.z += J.z * body.invMass;
    const dw = mul(body.invInertia, L);
    body.ang.x += dw.x; body.ang.y += dw.y; body.ang.z += dw.z;
    impulse.x += J.x; impulse.y += J.y; impulse.z += J.z;
    angular.x += L.x; angular.y += L.y; angular.z += L.z;
  };
  rows.forEach((r, i) => {
    if (!r) return;
    // Slightly decayed: four patches over-determine a car at rest, and a full warm start lets them build up opposing
    // impulses that hold nothing.
    let jx = WARM * state.jx[i], jy = WARM * state.jy[i];
    if (r.locked) { const m = Math.hypot(jx, jy); if (m > r.cap) { jx *= r.cap / m; jy *= r.cap / m; } }
    else { jx = Math.min(r.hi, Math.max(r.lo, jx)); jy = Math.min(r.capY, Math.max(-r.capY, jy)); }
    apply(r, r.jd + jx, jy);
    r.jx = jx; r.jy = jy;
  });
  for (let k = 0; k < ITERATIONS; k++) {
    for (const r of rows) {
      if (!r) continue;
      const w = r.w;
      const vx = dot(velocity(body, r.rx), w.forward), vy = dot(velocity(body, r.ry), w.side);
      let jx = r.jx - r.mx * vx, jy = r.jy - r.my * vy;
      if (r.locked) {
        const m = Math.hypot(jx, jy);
        if (m > r.cap) { jx *= r.cap / m; jy *= r.cap / m; }
      } else {
        jx = Math.min(r.hi, Math.max(r.lo, jx));
        jy = Math.min(r.capY, Math.max(-r.capY, jy));
      }
      apply(r, jx - r.jx, jy - r.jy);
      r.jx = jx; r.jy = jy;
    }
  }
  for (let i = 0; i < n; i++) {
    const r = rows[i];
    if (!r) continue;
    out[i].fx = (r.jx + r.jd) / dt; out[i].fy = r.jy / dt;
    state.jx[i] = r.jx; state.jy[i] = r.jy;
  }
  return { impulse, angular };
}

function velocity(b: BodyState, r: V3): V3 {
  const w = b.ang;
  return { x: b.lin.x + w.y * r.z - w.z * r.y, y: b.lin.y + w.z * r.x - w.x * r.z, z: b.lin.z + w.x * r.y - w.y * r.x };
}
