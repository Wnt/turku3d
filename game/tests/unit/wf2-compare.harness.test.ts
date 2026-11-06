// Wreckfest 2 comparison harness: headless Rapier runs of three standard manoeuvres (skidpad, straight braking,
// step steer) plus static/ride numbers, on the game's real vehicle path: PhysicsWorld.fixedStep at DT = 1/60 s, the
// same Vehicle.step -> world.step -> afterStep sequence the physics worker runs, driven through VehicleInput.
// Measurement only: it changes no tuning. Every physics step is logged to CSV; the summary goes to Markdown.
//   WF2_COMPARE=1 npx vitest run tests/unit/wf2-compare.harness.test.ts
import { test } from 'vitest';
import * as R from '@dimforge/rapier3d';
import fs from 'node:fs';
import path from 'node:path';
import { execSync } from 'node:child_process';
import { PhysicsWorld } from '../../src/physics/world';
import { DT, VEHICLES, type VehicleKind, type VehicleTuning } from '../../src/physics/constants';
import { GROUP } from '../../src/shared/col/materials';
import { handling } from '../../src/vehicle/handling';
import { rotate } from '../../src/vehicle/math';
import type { VehicleInput } from '../../src/physics/protocol';
import { modelDamageBox, modelTuning } from '../../src/vehicle/modelTuning';
import { PLAYER_MODEL } from '../../src/actors/fleet/catalog';

const G = 9.81;
const RUNS = process.env.WF2_RUNS ?? '/Users/wnt/turku3d/private/wreckfest/turku3d-runs';
const REPORT = process.env.WF2_REPORT ?? '/Users/wnt/turku3d/research/wreckfest2/turku3d-handling.md';
/** `golf` is the starter car exactly as main.ts spawns it: the PLAYER_MODEL tuning (vehicle/modelTuning.ts) with its damage cage. */
type RigKind = VehicleKind | 'golf';
const KINDS: RigKind[] = (process.env.WF2_KINDS ?? 'golf').split(',') as RigKind[];
const SPAWN_Y: Record<RigKind, number> = { car: 0.75, golf: 0.75, truck: 1.0, scooter: 0.6 };
const LABEL: Record<RigKind, string> = { golf: 'Starter Golf (`golf7`, modelTuning)', car: 'Car (`CAR`)', truck: 'Trophy truck (`TRUCK`)', scooter: 'Scooter' };
const tuningOf = (kind: RigKind): VehicleTuning => kind === 'golf' ? modelTuning(PLAYER_MODEL)! : VEHICLES[kind];
const WHEEL = ['fl', 'fr', 'rl', 'rr'];
/** Highest chassis speed at which the harness found the body asleep and woke it (it should only sleep at rest). */
const WAKE_SPEED = { max: 0 };

type V3 = { x: number; y: number; z: number };
const deg = (r: number) => r * 180 / Math.PI;
const rad = (d: number) => d * Math.PI / 180;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const mean = (a: number[]) => a.length ? a.reduce((s, v) => s + v, 0) / a.length : NaN;
const idle: VehicleInput = { throttle: 0, brake: 0, steer: 0, handbrake: false };

interface WheelSample { comp: number; load: number; fx: number; fy: number; alpha: number; kappa: number; contact: number }
interface Sample {
  t: number; x: number; y: number; z: number; psi: number; speed: number; vFwd: number; vLat: number;
  ax: number; ay: number; yawRate: number; yawAngvel: number; roll: number; pitch: number; steer: number;
  throttle: number; brake: number; stick: number; wheels: WheelSample[];
}

/** Least-squares slope and intercept. */
function linfit(xs: number[], ys: number[]) {
  const n = xs.length, mx = mean(xs), my = mean(ys);
  let sxx = 0, sxy = 0;
  for (let i = 0; i < n; i++) { sxx += (xs[i] - mx) ** 2; sxy += (xs[i] - mx) * (ys[i] - my); }
  const slope = n >= 2 && sxx > 0 ? sxy / sxx : NaN;
  return { slope, intercept: my - slope * mx, n };
}

/** Fits y(t) = c + e^(-ζωt)(A cos ωd t + B sin ωd t): coarse then fine grid over ζ and fn, linear least squares for c, A, B. */
function fitSecondOrder(ts: number[], ys: number[]) {
  const n = ts.length;
  const sse = (fn: number, zeta: number) => {
    const w = 2 * Math.PI * fn, s = zeta * w, q = Math.sqrt(Math.abs(1 - zeta * zeta)) * w;
    const U = new Float64Array(n), V = new Float64Array(n);
    let a11 = 0, a12 = 0, a13 = 0, a22 = 0, a23 = 0, a33 = 0, b1 = 0, b2 = 0, b3 = 0;
    for (let i = 0; i < n; i++) {
      const t = ts[i], e = Math.exp(-s * t);
      // Under-damped: e^(-σt)·{cos, sin}(ωd t); over-damped: e^(-(σ∓q)t); critical: e^(-ωt)·{1, t}.
      const u = U[i] = zeta < 1 ? e * Math.cos(q * t) : zeta > 1 ? Math.exp(-(s - q) * t) : e;
      const v = V[i] = zeta < 1 ? e * Math.sin(q * t) : zeta > 1 ? Math.exp(-(s + q) * t) : t * e, y = ys[i];
      a11 += 1; a12 += u; a13 += v; a22 += u * u; a23 += u * v; a33 += v * v; b1 += y; b2 += u * y; b3 += v * y;
    }
    const det = a11 * (a22 * a33 - a23 * a23) - a12 * (a12 * a33 - a23 * a13) + a13 * (a12 * a23 - a22 * a13);
    if (Math.abs(det) < 1e-24) return { e: Infinity, c: NaN };
    const c = (b1 * (a22 * a33 - a23 * a23) - a12 * (b2 * a33 - a23 * b3) + a13 * (b2 * a23 - a22 * b3)) / det;
    const A = (a11 * (b2 * a33 - a23 * b3) - b1 * (a12 * a33 - a23 * a13) + a13 * (a12 * b3 - b2 * a13)) / det;
    const B = (a11 * (a22 * b3 - b2 * a23) - a12 * (a12 * b3 - b2 * a13) + b1 * (a12 * a23 - a22 * a13)) / det;
    let e = 0;
    for (let i = 0; i < n; i++) e += (c + A * U[i] + B * V[i] - ys[i]) ** 2;
    return { e, c };
  };
  let best = { fn: NaN, zeta: NaN, e: Infinity, c: NaN };
  const scan = (f0: number, f1: number, df: number, z0: number, z1: number, dz: number) => {
    for (let zeta = Math.max(0.01, z0); zeta <= Math.min(2.5, z1) + 1e-12; zeta += dz) {
      if (Math.abs(zeta - 1) < 1e-4) continue;
      for (let fn = Math.max(0.2, f0); fn <= f1 + 1e-12; fn += df) {
        const r = sse(fn, zeta);
        if (r.e < best.e) best = { fn, zeta, e: r.e, c: r.c };
      }
    }
  };
  scan(0.3, 8, 0.05, 0.01, 2.5, 0.02);
  const { fn, zeta } = best;
  scan(fn - 0.06, fn + 0.06, 0.002, zeta - 0.025, zeta + 0.025, 0.001);
  const span = Math.max(...ys) - Math.min(...ys);
  return { fn: best.fn, zeta: best.zeta, c: best.c, rms: Math.sqrt(best.e / n), fitError: Math.sqrt(best.e / n) / (span || 1) };
}

/** First time a series crosses `level` going in direction `dir` (+1 up, -1 down), linearly interpolated. */
function crossing(ts: number[], ys: number[], level: number, dir: 1 | -1, from = 0) {
  for (let i = Math.max(1, from); i < ys.length; i++) {
    const a = (ys[i - 1] - level) * dir, b = (ys[i] - level) * dir;
    if (a < 0 && b >= 0) return ts[i - 1] + (ts[i] - ts[i - 1]) * (-a / (b - a));
    if (i === Math.max(1, from) && a >= 0) return ts[i - 1];
  }
  return NaN;
}

class SpeedPI {
  private i = 0;
  constructor(private kp = 0.6, private ki = 0.5, private lo = -0.15) {}
  update(target: number, v: number) {
    const e = target - v, out = this.kp * e + this.i;
    if ((out < 1 || e < 0) && (out > this.lo || e > 0)) this.i = clamp(this.i + this.ki * e * DT, this.lo, 1);
    return clamp(this.kp * e + this.i, this.lo, 1);
  }
}

class Rig {
  readonly pw = new PhysicsWorld(R);
  readonly t: VehicleTuning;
  readonly log: Sample[] = [];
  readonly comLocal: V3;
  time = 0;
  private prevV: V3 | null = null;
  private prevPsi: number | null = null;
  private lastInput: VehicleInput = idle;

  /** `slope`: the road rises along +X by this gradient (rise/run); its surface passes through the origin. */
  constructor(readonly kind: RigKind, slope = 0) {
    this.t = tuningOf(kind);
    this.comLocal = { x: 0, y: -this.t.comDrop, z: 0 };
    // A flat road slab with the same groups as a solid tile collider (physics/world.ts applyClass 'solid').
    const th = Math.atan(slope);
    this.pw.world.createCollider(R.ColliderDesc.cuboid(4000, 0.5, 4000).setTranslation(0.5 * Math.sin(th), -0.5 * Math.cos(th), 0)
      .setRotation({ x: 0, y: 0, z: Math.sin(th / 2), w: Math.cos(th / 2) }).setFriction(0.85)
      .setCollisionGroups(((GROUP.STATIC_SOLID & 0xffff) << 16) | ((GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC | 0xff00) & 0xffff)));
    this.pw.spawnVehicle({ x: 0, y: SPAWN_Y[kind], z: 0 }, 0, kind === 'golf'
      ? { tuning: this.t, damage: { box: modelDamageBox(PLAYER_MODEL)! } } : { kind });
    this.pw.driveVehicle('player');
  }

  get car() { return this.pw.vehicle!; }
  get wheelbase() { return this.t.wheels[0].x - this.t.wheels[2].x; }
  get track() { return this.t.wheels[1].z - this.t.wheels[0].z; }
  /** Speed-dependent steering lock (rad) from the game's own handling(). */
  lock(speed: number) { return -handling({ ...idle, steer: 1 }, speed, this.t).steer; }
  forwardSpeed() {
    const b = this.car.body, f = rotate({ x: 1, y: 0, z: 0 }, b.rotation()), v = b.linvel();
    return v.x * f.x + v.y * f.y + v.z * f.z;
  }
  /** Stick value (game sign: negative = left) that asks for a road-wheel angle `delta` (rad, positive = left). */
  stickFor(delta: number) { return clamp(-delta / this.lock(this.forwardSpeed()), -1, 1); }
  com(): V3 {
    const b = this.car.body, p = b.translation(), o = rotate(this.comLocal, b.rotation());
    return { x: p.x + o.x, y: p.y + o.y, z: p.z + o.z };
  }
  heading() {
    const f = rotate({ x: 1, y: 0, z: 0 }, this.car.body.rotation());
    return Math.atan2(-f.z, f.x);
  }
  resetPrev() { this.prevV = null; this.prevPsi = null; }

  /** Steps the harness had to wake a sleeping chassis (it only sleeps while parked at rest). */
  wakes = 0;

  step(input: VehicleInput) {
    this.lastInput = input;
    if (this.car.body.isSleeping()) {
      this.car.body.wakeUp(); this.wakes++;
      WAKE_SPEED.max = Math.max(WAKE_SPEED.max, Math.hypot(this.car.body.linvel().x, this.car.body.linvel().z));
    }
    this.pw.fixedStep(null, input);
    this.time += DT;
    const s = this.sample();
    this.log.push(s);
    return s;
  }

  /** Runs `seconds` of fixed steps; `control` gives the input before each step, `each` may stop the run early. */
  run(seconds: number, control: (() => VehicleInput) | VehicleInput, each?: (s: Sample) => boolean | void) {
    const n = Math.round(seconds / DT);
    let s: Sample | null = null;
    for (let i = 0; i < n; i++) {
      s = this.step(typeof control === 'function' ? control() : control);
      if (each?.(s)) break;
    }
    return s!;
  }

  private sample(): Sample {
    const car = this.car, b = car.body, v = car.controller, t = this.t, input = this.lastInput;
    const q = b.rotation(), lv = b.linvel(), w = b.angvel(), p = this.com();
    const f = rotate({ x: 1, y: 0, z: 0 }, q), side = rotate({ x: 0, y: 0, z: 1 }, q);
    const hl = Math.hypot(f.x, f.z) || 1, fh = { x: f.x / hl, z: f.z / hl }, left = { x: fh.z, z: -fh.x };
    const prev = this.prevV ?? lv;
    const ax = ((lv.x - prev.x) * fh.x + (lv.z - prev.z) * fh.z) / DT, ay = ((lv.x - prev.x) * left.x + (lv.z - prev.z) * left.z) / DT;
    this.prevV = { x: lv.x, y: lv.y, z: lv.z };
    // Yaw rate from the heading change over the step: Rapier applies the 1.2 s⁻¹ body angular damping after it
    // integrates the rotation, so angvel() reads (1 + 1.2·DT) ≈ 2 % below the rate the body actually turned at.
    const psi = Math.atan2(-fh.z, fh.x), dpsi = this.prevPsi === null ? w.y * DT : Math.atan2(Math.sin(psi - this.prevPsi), Math.cos(psi - this.prevPsi));
    this.prevPsi = psi;
    const state = car.state();
    const wheels = t.wheels.map((cp, i): WheelSample => {
      const steer = v.wheelSteering(i) ?? 0, contact = v.wheelIsInContact(i);
      // Contact velocity at the wheel connection point, relative to the centre of mass.
      const r = rotate({ x: cp.x - this.comLocal.x, y: cp.y - this.comLocal.y, z: cp.z - this.comLocal.z }, q);
      const vx = lv.x + w.y * r.z - w.z * r.y, vy = lv.y + w.z * r.x - w.x * r.z, vz = lv.z + w.x * r.y - w.y * r.x;
      const fw = rotate({ x: Math.cos(steer), y: 0, z: -Math.sin(steer) }, q);
      const sw = rotate({ x: Math.sin(steer), y: 0, z: Math.cos(steer) }, q);
      const vl = vx * fw.x + vy * fw.y + vz * fw.z, vs = vx * sw.x + vy * sw.y + vz * sw.z;
      const slide = state.wheels[i].tyre?.longitudinal ?? 0;
      return {
        comp: t.rest - (v.wheelSuspensionLength(i) ?? t.rest), load: v.wheelSuspensionForce(i) ?? 0,
        // The slip-curve tyre (vehicle/tyres.ts) applies its own forces; Rapier's impulses are the old tyre's.
        fx: car.tyreForces ? car.tyreForces.fx[i] : (v.wheelForwardImpulse(i) ?? 0) / DT,
        fy: car.tyreForces ? car.tyreForces.fy[i] : (v.wheelSideImpulse(i) ?? 0) / DT,
        // Positive: the contact patch moves to the wheel's right of its heading (normal cornering in a left turn).
        alpha: contact && Math.abs(vl) > 0.5 ? deg(Math.atan2(vs, Math.abs(vl))) : 0,
        // The game's own longitudinal sliding estimate (tyreSlip.ts longitudinalSlip) over the wheel's rolling speed.
        kappa: contact && Math.abs(vl) > 1 ? slide / Math.abs(vl) : 0,
        contact: contact ? 1 : 0,
      };
    });
    return {
      t: this.time, x: p.x, y: p.y, z: p.z, psi, speed: Math.hypot(lv.x, lv.z),
      vFwd: lv.x * fh.x + lv.z * fh.z, vLat: lv.x * left.x + lv.z * left.z, ax, ay, yawRate: dpsi / DT, yawAngvel: w.y,
      roll: -deg(Math.asin(clamp(side.y, -1, 1))), pitch: deg(Math.asin(clamp(f.y, -1, 1))),
      // Road-wheel angle: the mean of the two front wheels (Ackermann toes them apart), WF's "input × 30°".
      steer: ((v.wheelSteering(0) ?? 0) + (v.wheelSteering(1) ?? 0)) / 2, throttle: input.throttle, brake: input.brake, stick: input.steer, wheels,
    };
  }

  writeCsv(name: string) {
    fs.mkdirSync(RUNS, { recursive: true });
    const head = ['t_s', 'x_m', 'y_com_m', 'z_m', 'heading_deg', 'speed_kmh', 'v_fwd_ms', 'v_lat_ms', 'ax_long_g', 'ay_lat_left_g',
      'yaw_rate_left_dps', 'angvel_y_dps', 'roll_right_down_deg', 'pitch_nose_up_deg', 'roadwheel_steer_left_deg', 'in_throttle', 'in_brake', 'in_steer',
      ...WHEEL.flatMap(w => [`${w}_comp_m`, `${w}_load_n`, `${w}_fx_n`, `${w}_fy_n`, `${w}_slip_angle_deg`, `${w}_slip_ratio_est`, `${w}_contact`])];
    const f6 = (v: number) => Number.isFinite(v) ? +v.toFixed(6) : '';
    const lines = this.log.map(s => [s.t, s.x, s.y, s.z, deg(s.psi), s.speed * 3.6, s.vFwd, s.vLat, s.ax / G, s.ay / G,
      deg(s.yawRate), deg(s.yawAngvel), s.roll, s.pitch, deg(s.steer), s.throttle, s.brake, s.stick,
      ...s.wheels.flatMap(w => [w.comp, w.load, w.fx, w.fy, w.alpha, w.kappa, w.contact])].map(f6).join(','));
    fs.writeFileSync(path.join(RUNS, `${this.kind}-${name}.csv`), [head.join(','), ...lines].join('\n') + '\n');
  }

  free() { this.pw.world.free(); }
}

// ------------------------------------------------------------------------------------------------ static / ride
function statics(kind: RigKind) {
  const rig = new Rig(kind), t = rig.t, M = t.mass, body = rig.car.body;
  rig.run(4, idle);
  const s0 = rig.log.at(-1)!;
  const loads = s0.wheels.map(w => w.load), total = loads.reduce((a, b) => a + b, 0);
  const inertia = body.principalInertia();
  const st = {
    comHeight: s0.y, frontShare: (loads[0] + loads[1]) / total, loads, comp: s0.wheels.map(w => w.comp),
    bellyClearance: body.translation().y - t.halfExt.y, inertia: { roll: inertia.x, yaw: inertia.y, pitch: inertia.z },
    angularDamping: body.angularDamping(), weight: total,
  };
  // Analytic corner numbers from Rapier's suspension law F = M·(k·(rest − L) − c·dL/dt) per wheel.
  const k = t.suspensionStiffness * M;
  const corner = loads.map(F => {
    const m = F / G;
    return { m, fn: Math.sqrt(k / m) / (2 * Math.PI), zc: t.suspensionCompression * M / (2 * Math.sqrt(k * m)), zr: t.suspensionRelaxation * M / (2 * Math.sqrt(k * m)) };
  });
  // Heave: release the settled car 5 cm higher with zero velocity.
  const settle = () => {
    body.setTranslation({ x: 0, y: SPAWN_Y[kind], z: 0 }, true);
    body.setRotation({ x: 0, y: 0, z: 0, w: 1 }, true);
    body.setLinvel({ x: 0, y: 0, z: 0 }, true); body.setAngvel({ x: 0, y: 0, z: 0 }, true);
    rig.resetPrev(); rig.run(4, idle);
  };
  const release = (lift: number, pitchRad: number) => {
    const p = body.translation();
    body.setTranslation({ x: p.x, y: p.y + lift, z: p.z }, true);
    body.setRotation({ x: 0, y: 0, z: Math.sin(pitchRad / 2), w: Math.cos(pitchRad / 2) }, true);
    body.setLinvel({ x: 0, y: 0, z: 0 }, true); body.setAngvel({ x: 0, y: 0, z: 0 }, true);
    rig.resetPrev();
    const k0 = rig.log.length, t0 = rig.time;
    rig.run(3, idle);
    return rig.log.slice(k0).map(s => ({ ...s, t: s.t - t0 }));
  };
  settle();
  const heave = release(0.05, 0);
  const ts = heave.map(s => s.t);
  const heaveFit = fitSecondOrder(ts, heave.map(s => s.y));
  const frontFit = fitSecondOrder(ts, heave.map(s => (s.wheels[0].comp + s.wheels[1].comp) / 2));
  const rearFit = fitSecondOrder(ts, heave.map(s => (s.wheels[2].comp + s.wheels[3].comp) / 2));
  const heaveMin = Math.min(...heave.map(s => s.y));
  const heaveOvershoot = (heaveFit.c - heaveMin) / (heave[0].y - heaveFit.c);
  const heaveContactLost = heave.some(s => s.wheels.some(w => !w.contact));
  // Pitch: nose 5 cm up at the front axle, rear axle at its static height.
  settle();
  const L = rig.wheelbase, rearArm = -t.wheels[2].x, theta = 0.05 / L;
  const pitch = release(rearArm * theta, theta);
  const pts = pitch.map(s => s.t);
  const pitchFit = fitSecondOrder(pts, pitch.map(s => s.pitch));
  const pitchPeak = Math.min(...pitch.map(s => s.pitch)), pitchOvershoot = (pitchFit.c - pitchPeak) / (pitch[0].pitch - pitchFit.c);
  rig.writeCsv('static-drop');
  rig.free();
  return { ...st, k, corner, heaveFit, frontFit, rearFit, heaveOvershoot, heaveContactLost, pitchFit, pitchOvershoot, pitchInit: deg(theta), L, wakes: rig.wakes };
}

// ------------------------------------------------------------------------------------------------ skidpad
interface SkidRow {
  target: number; speed: number; radius: number; ay: number; ayKin: number; steer: number; ackermann: number; roll: number;
  alphaF: number; alphaR: number; beta: number; loads: number[]; err: number; errMax: number; throttle: number;
  lock: number; held: boolean; why: string; lltArm?: number; lltdFront?: number;
}

function skidpad(kind: RigKind, R0: number, speeds: number[]) {
  const rig = new Rig(kind), t = rig.t, L = rig.wheelbase, b = -t.wheels[2].x;
  rig.run(1, idle);
  const pi = new SpeedPI();
  const Ki = 0.04;
  let I = 0;
  const radial = (s: { x: number; z: number }) => Math.hypot(s.x, -s.z - R0) - R0;
  const control = (target: number) => () => {
    const p = rig.com(), psi = rig.heading(), v = Math.max(1, rig.forwardSpeed());
    // Pure pursuit from the rear axle on the circle centred at (X, Y) = (0, R0), with Y = −z (left turns, CCW).
    const rx = p.x - b * Math.cos(psi), ry = -p.z - b * Math.sin(psi);
    const phi = Math.atan2(ry - R0, rx), ld = clamp(0.5 * v, 4, 12), phiT = phi + ld / R0;
    const tx = R0 * Math.cos(phiT) - rx, ty = R0 + R0 * Math.sin(phiT) - ry;
    const alpha = Math.atan2(ty, tx) - psi, a = Math.atan2(Math.sin(alpha), Math.cos(alpha));
    const e = radial(p);
    const lock = rig.lock(rig.forwardSpeed());
    const pp = Math.atan(2 * L * Math.sin(a) / Math.hypot(tx, ty));
    const delta = pp + I;
    if (Math.abs(delta) < lock || Math.sign(e) !== Math.sign(I)) I = clamp(I + Ki * e * DT, -0.3, 0.3);
    return { throttle: pi.update(target, rig.forwardSpeed()), brake: 0, steer: rig.stickFor(delta), handbrake: false };
  };
  const rows: SkidRow[] = [];
  for (const [k, kmh] of speeds.entries()) {
    const target = kmh / 3.6, dur = k === 0 ? 10 : 6;
    const from = rig.log.length;
    // The speed rises to each step over 2 s (a step input saturates a driven front axle's grip with throttle).
    const was = k === 0 ? target : speeds[k - 1] / 3.6, t0 = rig.time;
    rig.run(dur, () => control(was + (target - was) * Math.min(1, (rig.time - t0) / 2))());
    const w = rig.log.slice(from).filter(s => s.t > rig.time - 2 + 1e-9);
    const speed = mean(w.map(s => s.speed)), r = mean(w.map(s => s.yawRate));
    const errs = w.map(s => radial(s));
    const ay = mean(w.map(s => s.ay)) / G;
    const radius = speed / r;
    const steer = deg(mean(w.map(s => s.steer)));
    const beta = mean(w.map(s => deg(Math.atan2(s.vLat, s.vFwd))));
    const lockDeg = deg(rig.lock(mean(w.map(s => s.vFwd))));
    const err = mean(errs), errMax = Math.max(...errs.map(Math.abs));
    let why = '';
    if (Math.abs(speed * 3.6 - kmh) > 1) why = `speed ${(speed * 3.6).toFixed(1)} km/h`;
    else if (Math.abs(err) > 0.5 || errMax > 1) why = steer > lockDeg - 0.05 ? 'steering at the speed-dependent lock, path drifts out' : 'radial error > 0.5 m';
    else if (Math.abs(beta) > 10) why = 'spin';
    rows.push({
      target: kmh, speed: speed * 3.6, radius, ay, ayKin: speed * r / G, steer, ackermann: deg(L / radius), roll: mean(w.map(s => s.roll)),
      alphaF: mean(w.map(s => (s.wheels[0].alpha + s.wheels[1].alpha) / 2)), alphaR: mean(w.map(s => (s.wheels[2].alpha + s.wheels[3].alpha) / 2)),
      beta, loads: [0, 1, 2, 3].map(i => mean(w.map(s => s.wheels[i].load))), err, errMax,
      throttle: mean(w.map(s => s.throttle)), lock: lockDeg, held: !why, why,
    });
    // Lateral load transfer: per-axle (outer − inner) / 2, and the moment arm it implies for the measured lateral force.
    const x = rows.at(-1)!, dF = (x.loads[1] - x.loads[0]) / 2, dR = (x.loads[3] - x.loads[2]) / 2;
    Object.assign(x, { lltArm: (dF + dR) * rig.track / (t.mass * ay * G), lltdFront: dF / (dF + dR) });
    if (why) break;
  }
  rig.writeCsv(`skidpad-r${R0}`);
  rig.free();
  return rows;
}

/** Full stick held with a PI speed hold: the largest steady lateral g the steering lock allows at each speed. */
function fullStick(kind: RigKind, speeds: number[]) {
  const rig = new Rig(kind), L = rig.wheelbase;
  rig.run(1, idle);
  const pi = new SpeedPI();
  const rows = [];
  for (const [k, kmh] of speeds.entries()) {
    const target = kmh / 3.6, from = rig.log.length;
    rig.run(k === 0 ? 10 : 6, () => ({ throttle: pi.update(target, rig.forwardSpeed()), brake: 0, steer: -1, handbrake: false }));
    const w = rig.log.slice(from).filter(s => s.t > rig.time - 2 + 1e-9);
    const speed = mean(w.map(s => s.speed)), r = mean(w.map(s => s.yawRate)), steer = deg(mean(w.map(s => s.steer)));
    const yawStd = Math.sqrt(mean(w.map(s => (s.yawRate - r) ** 2)));
    rows.push({
      target: kmh, speed: speed * 3.6, radius: speed / r, ay: mean(w.map(s => s.ay)) / G, steer, ackermann: deg(L * r / speed),
      roll: mean(w.map(s => s.roll)), beta: mean(w.map(s => deg(Math.atan2(s.vLat, s.vFwd)))),
      alphaF: mean(w.map(s => (s.wheels[0].alpha + s.wheels[1].alpha) / 2)), alphaR: mean(w.map(s => (s.wheels[2].alpha + s.wheels[3].alpha) / 2)),
      throttle: mean(w.map(s => s.throttle)), steady: Math.abs(speed * 3.6 - kmh) < 1 && yawStd < 0.02 * Math.abs(r),
      kappaR: mean(w.map(s => (s.wheels[2].kappa + s.wheels[3].kappa) / 2)),
    });
    if (Math.abs(speed * 3.6 - kmh) > 1.5) break;
  }
  rig.writeCsv('fullstick');
  rig.free();
  return rows;
}

/** Full throttle, straight, for 60 s: the speed it settles at. */
function topSpeed(kind: RigKind) {
  const rig = new Rig(kind);
  rig.run(1, idle);
  const s = rig.run(60, { throttle: 1, brake: 0, steer: 0, handbrake: false });
  rig.free();
  return s.vFwd * 3.6;
}

// ------------------------------------------------------------------------------------------------ braking
function cruise(rig: Rig, target: number, holdSeconds: number, maxSeconds = 45) {
  const pi = new SpeedPI();
  let held = 0;
  rig.run(maxSeconds, () => ({ throttle: pi.update(target, rig.forwardSpeed()), brake: 0, steer: 0, handbrake: false }),
    s => { held = Math.abs(s.vFwd - target) < 0.1 ? held + DT : 0; return held >= holdSeconds; });
  return held >= holdSeconds;
}

function braking(kind: RigKind, kmh: number) {
  const rig = new Rig(kind), body = rig.car.body;
  rig.run(2, idle);
  const staticShare = (() => { const w = rig.log.at(-1)!.wheels; return (w[0].load + w[1].load) / w.reduce((a, x) => a + x.load, 0); })();
  const v0 = kmh / 3.6;
  const reached = cruise(rig, v0, 3);
  const plateau = rig.log.at(-1)!.vFwd * 3.6;
  const cruiseShare = mean(rig.log.slice(-30).map(s => (s.wheels[0].load + s.wheels[1].load) / s.wheels.reduce((a, x) => a + x.load, 0)));
  let injected = false;
  if (!reached) {
    // Above the reachable speed: set the chassis velocity to v0 along the heading for the last step before braking.
    const f = rotate({ x: 1, y: 0, z: 0 }, body.rotation()), hl = Math.hypot(f.x, f.z), lv = body.linvel();
    body.setLinvel({ x: f.x / hl * v0, y: lv.y, z: f.z / hl * v0 }, true);
    rig.resetPrev(); rig.step({ ...idle }); injected = true;
  }
  const kb = rig.log.length, tIn = rig.time, start = rig.com(), psi0 = rig.heading();
  const vStart = rig.forwardSpeed();
  rig.run(25, { throttle: 0, brake: 1, steer: 0, handbrake: false }, s => s.vFwd <= 0);
  const B = rig.log.slice(kb), ts = B.map(s => s.t), vs = B.map(s => s.vFwd), dec = B.map(s => -s.ax / G);
  const t90 = crossing(ts, vs, 0.9 * vStart, -1), t10 = crossing(ts, vs, 0.1 * vStart, -1), tStop = crossing(ts, vs, 0, -1);
  const smooth = dec.map((_, i) => mean(dec.slice(Math.max(0, i - 2), i + 3)));
  const peak = Math.max(...dec), peakSmooth = Math.max(...smooth);
  const t90peak = ts[dec.findIndex(d => d >= 0.9 * peak)] - tIn;
  const win = B.filter(s => s.t >= t90 && s.t <= t10);
  // Distance along the initial heading to the (interpolated) stop.
  const last = B.at(-1)!, prevS = B.at(-2)!;
  const frac = last.vFwd < 0 && prevS.vFwd > 0 ? prevS.vFwd / (prevS.vFwd - last.vFwd) : 1;
  const stopX = prevS.x + (last.x - prevS.x) * frac, stopZ = prevS.z + (last.z - prevS.z) * frac;
  const dist = (stopX - start.x) * Math.cos(psi0) - (stopZ - start.z) * Math.sin(psi0);
  const share = mean(win.map(s => (s.wheels[0].load + s.wheels[1].load) / s.wheels.reduce((a, x) => a + x.load, 0)));
  const dragG = (0.015 + 0.004 * vStart) * vStart / G;
  const res = {
    kmh, reached, plateau, injected, vStart: vStart * 3.6, meanDecel: 0.8 * vStart / (t10 - t90) / G, peak, peakSmooth, t90peak,
    stopDist: dist, stopTime: tStop - tIn, pitch: -mean(win.map(s => s.pitch)), peakPitch: -Math.min(...B.map(s => s.pitch)),
    frontShare: share, staticShare, cruiseShare, dragG,
    kappaF: mean(win.map(s => (s.wheels[0].kappa + s.wheels[1].kappa) / 2)), kappaR: mean(win.map(s => (s.wheels[2].kappa + s.wheels[3].kappa) / 2)),
    rearLift: B.some(s => !s.wheels[2].contact || !s.wheels[3].contact), lateral: Math.max(...B.map(s => Math.abs(s.vLat))),
    fxF: mean(win.map(s => -(s.wheels[0].fx + s.wheels[1].fx))), fxR: mean(win.map(s => -(s.wheels[2].fx + s.wheels[3].fx))),
  };
  rig.writeCsv(`brake-${kmh}`);
  rig.free();
  return res;
}

// ------------------------------------------------------------------------------------------------ step steer
function stepSteer(kind: RigKind, stepDeg: number | 'lock' | 'key', kmh = 60) {
  const rig = new Rig(kind);
  rig.run(2, idle);
  const v0 = kmh / 3.6;
  const pi = new SpeedPI();
  let held = 0;
  rig.run(45, () => ({ throttle: pi.update(v0, rig.forwardSpeed()), brake: 0, steer: 0, handbrake: false }),
    s => { held = Math.abs(s.vFwd - v0) < 0.05 ? held + DT : 0; return held >= 4; });
  const throttle = mean(rig.log.slice(-60).map(s => s.throttle));
  const k0 = rig.log.length, tCmd = rig.time, vCmd = rig.forwardSpeed();
  const lock0 = rig.lock(vCmd);
  const target = typeof stepDeg === 'string' ? lock0 : rad(stepDeg);
  // 'key': the left key held, as a keyboard player does (VehicleInput.steerKey: the car slews it, tuning.keySteer).
  rig.run(4, () => ({ throttle, brake: 0, steer: typeof stepDeg === 'string' ? -1 : rig.stickFor(target), handbrake: false, steerKey: stepDeg === 'key' }));
  const S = rig.log.slice(k0), ts = [tCmd, ...S.map(s => s.t)];
  const steer = [rig.log[k0 - 1].steer, ...S.map(s => s.steer)], yaw = [rig.log[k0 - 1].yawRate, ...S.map(s => s.yawRate)];
  const t50 = crossing(ts, steer, 0.5 * target, 1);
  const steer90 = crossing(ts, steer, 0.9 * target, 1) - tCmd, steer100 = crossing(ts, steer, 0.999 * target, 1) - tCmd;
  const win = S.filter(s => s.t > tCmd + 2 && s.t <= tCmd + 3 + 1e-9);
  const rss = mean(win.map(s => s.yawRate)), ayss = mean(win.map(s => s.ay)) / G;
  const upTo3 = S.filter(s => s.t <= tCmd + 3 + 1e-9);
  let pk = 0, tPk = NaN;
  for (const s of upTo3) if (s.yawRate > pk) { pk = s.yawRate; tPk = s.t; }
  const t10 = crossing(ts, yaw, 0.1 * rss, 1), t90 = crossing(ts, yaw, 0.9 * rss, 1);
  let settled = tCmd;
  for (const s of upTo3) if (Math.abs(s.yawRate - rss) > 0.05 * Math.abs(rss)) settled = s.t;
  const res = {
    kind, stepDeg, target: deg(target), lock0: deg(lock0), speed0: vCmd * 3.6, throttle,
    steerSteady: deg(mean(win.map(s => s.steer))), steer90, steer100,
    yawSS: deg(rss), aySS: ayss, speedWin: mean(win.map(s => s.speed)) * 3.6, speed4: S.at(-1)!.speed * 3.6,
    rise: t90 - t10, d10: t10 - t50, d90: t90 - t50, tPeak: tPk - t50, overshoot: (pk - rss) / rss * 100,
    settle: settled - t50, kinematicRatio: rss / (mean(win.map(s => s.speed)) * mean(win.map(s => s.steer)) / rig.wheelbase),
    beta: mean(win.map(s => deg(Math.atan2(s.vLat, s.vFwd)))), roll: mean(win.map(s => s.roll)),
    alphaF: mean(win.map(s => (s.wheels[0].alpha + s.wheels[1].alpha) / 2)), alphaR: mean(win.map(s => (s.wheels[2].alpha + s.wheels[3].alpha) / 2)),
    peakRoll: Math.max(...S.map(s => s.roll)), wheelLift: S.some(s => s.wheels.some(w => !w.contact)),
  };
  rig.writeCsv(`stepsteer-${typeof stepDeg === 'string' ? stepDeg : stepDeg + 'deg'}`);
  rig.free();
  return res;
}

// ------------------------------------------------------------------------------------------------ drivability
interface Check { name: string; value: string; ok: boolean }
/** Sideslip of the chassis (deg, + the nose inside a left turn). */
const sideslip = (s: Sample) => deg(Math.atan2(s.vLat, Math.max(0.5, Math.abs(s.vFwd))));

/** Pure pursuit from the rear axle on a path x → z (left is −z) with yaw-rate damping against the path's own yaw
 *  rate (as Wreckfest 2's path law): the stick for the road-wheel angle it asks. */
function pursue(rig: Rig, path: (x: number) => number) {
  const p = rig.com(), psi = rig.heading(), v = Math.max(1, rig.forwardSpeed()), L = rig.wheelbase, b = -rig.t.wheels[2].x;
  const rx = p.x - b * Math.cos(psi), rz = p.z + b * Math.sin(psi), ld = clamp(0.5 * v, 4, 12);
  const tx = rx + ld, tz = path(tx);
  const a = Math.atan2(-(tz - rz), tx - rx) - psi;
  // Path yaw rate at the car: v × curvature of x → −z (left positive).
  const h = 0.5, k = -(path(p.x + h) - 2 * path(p.x) + path(p.x - h)) / (h * h);
  const r = rig.log.at(-1)?.yawRate ?? 0;
  return rig.stickFor(Math.atan(2 * L * Math.sin(a) / ld) + 0.06 * (v * k - r));
}

/** Driven checks for the failure modes the tuning must avoid: a slalom, lift-off, braking and full throttle in a
 *  steady corner, a handbrake turn, and creep at rest and on a 10 % slope. */
function drivability(kind: RigKind): Check[] {
  const checks: Check[] = [];
  for (const kmh of [40, 50, 60, 70]) {
    const rig = new Rig(kind), pi = new SpeedPI(), v0 = kmh / 3.6;
    rig.run(1, idle);
    rig.run(20, () => ({ throttle: pi.update(v0, rig.forwardSpeed()), brake: 0, steer: pursue(rig, () => 0), handbrake: false }));
    // 18 m between cones (36 m wavelength), ±1.5 m, as Wreckfest 2's slalom.
    const x0 = rig.com().x, path = (x: number) => x < x0 ? 0 : -1.5 * Math.sin(2 * Math.PI * (x - x0) / 36);
    const k = rig.log.length;
    rig.run(4 * 36 / v0, () => ({ throttle: pi.update(v0, rig.forwardSpeed()), brake: 0, steer: pursue(rig, path), handbrake: false }));
    const S = rig.log.slice(k), err = S.map(s => s.z - path(s.x));
    const beta = Math.max(...S.map(s => Math.abs(sideslip(s)))), ay = Math.max(...S.map(s => Math.abs(s.ay))) / G;
    const rms = Math.sqrt(mean(err.map(e => e * e))), rear = Math.max(...S.map(s => Math.abs(s.wheels[2].alpha)));
    checks.push({ name: `slalom 18 m at ${kmh} km/h`, value: `path error rms ${f(rms, 2)} m, peak ${f(ay, 2)} g, peak sideslip ${f(beta, 1)}°, peak rear slip ${f(rear, 1)}°`, ok: beta < 15 });
    rig.writeCsv(`slalom-${kmh}`); rig.free();
  }
  // Steady left turn on R 30 m, then the steering held still and one input changed.
  const corner = (kmh: number, label: string, input: Partial<VehicleInput>, seconds: number) => {
    const rig = new Rig(kind), pi = new SpeedPI(), R0 = 30, v0 = kmh / 3.6;
    rig.run(1, idle);
    const path = (x: number) => -(R0 - Math.sqrt(Math.max(0, R0 * R0 - x * x)));
    // Settle on the circle: pursue the circle through its arc ahead (x along the chord from the start).
    const circle = () => {
      const p = rig.com(), phi = Math.atan2(p.x, R0 + p.z), ahead = phi + clamp(0.5 * rig.forwardSpeed(), 4, 12) / R0;
      const tx = R0 * Math.sin(ahead), tz = -(R0 - R0 * Math.cos(ahead)), psi = rig.heading(), L = rig.wheelbase;
      const a = Math.atan2(-(tz - p.z), tx - p.x) - psi, d = Math.hypot(tx - p.x, tz - p.z);
      return rig.stickFor(Math.atan(2 * L * Math.sin(Math.atan2(Math.sin(a), Math.cos(a))) / d));
    };
    void path;
    rig.run(12, () => ({ throttle: pi.update(v0, rig.forwardSpeed()), brake: 0, steer: circle(), handbrake: false }));
    const held = mean(rig.log.slice(-30).map(s => s.stick)), thr = mean(rig.log.slice(-30).map(s => s.throttle));
    const before = rig.log.at(-1)!, k = rig.log.length;
    rig.run(seconds, { throttle: thr, brake: 0, steer: held, handbrake: false, ...input });
    const S = rig.log.slice(k), beta = Math.max(...S.map(s => Math.abs(sideslip(s))));
    const yaw = Math.max(...S.map(s => deg(s.yawRate))), last = S.at(-1)!;
    checks.push({ name: `${label} on R 30 m at ${kmh} km/h (${f(before.ay / G, 2)} g)`, value: `peak sideslip ${f(beta, 1)}°, peak yaw ${f(yaw, 0)}°/s (from ${f(deg(before.yawRate), 0)}), end ${f(last.speed * 3.6, 0)} km/h`, ok: beta < 20 });
    rig.writeCsv(`corner-${label.replace(/\W+/g, '-')}-${kmh}`); rig.free();
  };
  corner(55, 'lift-off', { throttle: 0 }, 2);
  corner(55, 'brake 0.5', { throttle: 0, brake: 0.5 }, 2.5);
  corner(55, 'full brake', { throttle: 0, brake: 1 }, 2.5);
  corner(40, 'full throttle', { throttle: 1 }, 3);
  {
    const rig = new Rig(kind), pi = new SpeedPI();
    rig.run(1, idle);
    rig.run(15, () => ({ throttle: pi.update(50 / 3.6, rig.forwardSpeed()), brake: 0, steer: pursue(rig, () => 0), handbrake: false }));
    const psi0 = rig.heading(), k = rig.log.length;
    rig.run(0.7, { throttle: 0, brake: 0, steer: -1, handbrake: true });
    rig.run(3, { throttle: 0, brake: 0, steer: 0, handbrake: false });
    const S = rig.log.slice(k), beta = Math.max(...S.map(s => Math.abs(sideslip(s))));
    let turned = 0, prev = psi0;
    for (const s of S) { turned += Math.atan2(Math.sin(s.psi - prev), Math.cos(s.psi - prev)); prev = s.psi; }
    const end = S.at(-1)!;
    checks.push({ name: 'handbrake turn at 50 km/h (full stick + handbrake 0.7 s, then hands off)', value: `turned ${f(deg(turned), 0)}°, peak sideslip ${f(beta, 0)}°, end sideslip ${f(sideslip(end), 1)}° at ${f(end.speed * 3.6, 0)} km/h`, ok: Math.abs(sideslip(end)) < 10 || end.speed < 1 });
    rig.writeCsv('handbrake-50'); rig.free();
  }
  for (const slope of [0, 0.1]) {
    const rig = new Rig(kind, slope), body = rig.car.body;
    // Settle parked (no driver: half the brakes, vehicle/handling.ts); a held brake pedal would reverse at rest.
    for (let i = 0; i < 180; i++) rig.pw.fixedStep(null, null);
    const p0 = { ...body.translation() };
    // Parked (no driver): the game holds half the brakes (vehicle/handling.ts).
    for (let i = 0; i < 600; i++) { rig.pw.fixedStep(null, null); }
    const p1 = { ...body.translation() }, moved = Math.hypot(p1.x - p0.x, p1.z - p0.z);
    // Rapier's substeps leave a steady 0.06 m/s sink in the chassis velocity at rest (see the static notes); on a slope
    // its along-slope share creeps a braked car a few cm per 10 s (Rapier's own tyre: CAR 16 cm downhill).
    checks.push({ name: `parked 10 s on ${slope * 100} % slope`, value: `moved ${f(moved * 100, 2)} cm`, ok: moved < 0.1 });
    rig.free();
  }
  return checks;
}

// ------------------------------------------------------------------------------------------------ report
const f = (v: number, d = 2) => Number.isFinite(v) ? v.toFixed(d) : 'n/a';
const table = (head: string[], rows: (string | number)[][]) =>
  [`| ${head.join(' | ')} |`, `|${head.map(() => '---').join('|')}|`, ...rows.map(r => `| ${r.join(' | ')} |`)].join('\n');

test.skipIf(!process.env.WF2_COMPARE)('WF2 comparison baseline: skidpad, braking, step steer, ride', () => {
  const repo = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../../..');
  const git = (cmd: string) => execSync(`git -C ${repo} ${cmd}`).toString().trim();
  const commit = git('rev-parse --short HEAD'), branch = git('rev-parse --abbrev-ref HEAD');
  const dirty = git('status --porcelain -- game') !== '';
  const speeds = Array.from({ length: 30 }, (_, i) => 20 + 5 * i);
  const out: Record<string, unknown> = {};
  const md: string[] = [];
  const notes: Record<string, Record<string, string>> = {};
  for (const kind of KINDS) {
    const t = tuningOf(kind);
    const st = statics(kind);
    const pads = [20, 30].map(r => ({ r, rows: skidpad(kind, r, speeds) }));
    const stick = fullStick(kind, [30, 40, 50, 60, 70, 80, 90, 100, 110, 120]);
    const brakes = [50, 80, 100].map(v => braking(kind, v));
    const top = topSpeed(kind);
    const checks = drivability(kind);
    const lock60 = deg(-handling({ ...idle, steer: 1 }, 60 / 3.6, t).steer);
    const steps = [2, 4, 8, ...(Math.abs(lock60 - 8) > 0.05 ? ['lock' as const] : []), ...(t.keySteer ? ['key' as const] : [])].map(d => stepSteer(kind, d));
    out[kind] = { st, pads, stick, brakes, steps, top, checks };
    console.log(`wf2 ${kind} ${JSON.stringify({ st, pads, stick, brakes, steps }, (_, v) => typeof v === 'number' ? +v.toFixed(4) : v)}`);

    const L = st.L, track = t.wheels[1].z - t.wheels[0].z;
    const fits = pads.map(({ r, rows }) => {
      const lin = rows.filter(x => x.held && x.ay < 0.5), held = rows.filter(x => x.held);
      const K = linfit(lin.map(x => x.ay), lin.map(x => x.steer - x.ackermann));
      const roll = linfit(lin.map(x => x.ay), lin.map(x => x.roll));
      const rollAll = linfit(held.map(x => x.ay), held.map(x => x.roll));
      const Kall = linfit(held.map(x => x.ay), held.map(x => x.steer - x.ackermann));
      const max = held.reduce((m, x) => x.ay > m.ay ? x : m, held[0]);
      const fail = rows.find(x => !x.held);
      const lltArm = mean(held.map(x => x.lltArm!)), lltdFront = mean(held.map(x => x.lltdFront!));
      return { r, K, Kall, roll, rollAll, max, fail, lltArm, lltdFront };
    });
    out[`${kind}-fits`] = fits;
    const lockSpeed = (r: number) => t.steerHalfSpeed * Math.sqrt(t.maxSteer * r / L - 1) * 3.6;
    const best = stick.filter(x => x.steady).reduce((m, x) => x.ay > m.ay ? x : m, stick[0]);
    notes[kind] = {
      lock: `${f(lockSpeed(20), 0)} / ${f(lockSpeed(30), 0)} km/h`,
      grip: `${f(best.ay, 2)} g at ${f(best.speed, 0)} km/h (front slip ${f(best.alphaF, 1)}° vs rear ${f(best.alphaR, 1)}°)`,
      roll: `${f(fits[0].lltArm, 3)} m vs CoM ${f(st.comHeight, 3)} m (${f(fits[0].lltArm / st.comHeight * 100, 0)} %)`,
      top: `${f(top, 1)} km/h after 60 s full throttle${brakes[2].injected ? '; 100 km/h braking starts from an injected velocity' : ''}`,
      brake: `${f(brakes[1].peak, 2)} g first step, ${f(brakes[1].meanDecel, 2)} g mean; rear tyres friction-limited (${f(brakes[1].fxR, 0)} of ${f(2 * t.brakeForce / DT, 0)} N)`,
      sag: `${f(st.comp[0] * 100, 2)} cm vs ${f(st.loads[0] / st.k * 100, 2)} cm`,
      lockStep: steps.find(x => x.stepDeg === 'lock') ? `${f(steps.find(x => x.stepDeg === 'lock')!.lock0, 2)}° at 60 → ${f(deg(-handling({ ...idle, steer: 1 }, steps.find(x => x.stepDeg === 'lock')!.speed4 / 3.6, t).steer), 2)}° at ${f(steps.find(x => x.stepDeg === 'lock')!.speed4, 1)} km/h` : 'n/a',
    };

    md.push(`### ${LABEL[kind]}`, '');
    md.push('#### Parameters', '');
    const brakeN = t.brakeForce / DT;
    md.push(table(['Parameter', 'Value'], [
      ['Mass', `${t.mass} kg`],
      ['Chassis box (collider)', `${(2 * t.halfExt.x).toFixed(2)} × ${(2 * t.halfExt.y).toFixed(2)} × ${(2 * t.halfExt.z).toFixed(2)} m (L × H × W)`],
      ['Inertia roll / yaw / pitch', `${f(st.inertia.roll, 0)} / ${f(st.inertia.yaw, 0)} / ${f(st.inertia.pitch, 0)} kg·m²`],
      ['Wheelbase / track', `${f(L, 3)} m / ${f(track, 3)} m`],
      ['CoM height (static, measured)', `${f(st.comHeight, 3)} m${t.comDrop ? ` (${t.comDrop} m below the box centre)` : ' (box centre)'}`],
      ['Static axle split front / rear (suspension forces)', `${f(st.frontShare * 100, 1)} / ${f(100 - st.frontShare * 100, 1)} %`],
      ['Tyre radius', `${t.wheelRadius} m`],
      ['Suspension rest length / travel', `${t.rest} m / ±${t.suspensionTravel} m`],
      ['Spring rate per wheel', `${f(st.k, 0)} N/m (Rapier stiffness ${t.suspensionStiffness} × mass)`],
      ['Static compression per wheel', `${f(st.comp[0] * 100, 2)} cm (k alone: ${f(st.loads[0] / st.k * 100, 2)} cm; the damper term carries the rest, see notes)`],
      ['Damping per wheel, bump / rebound', `${f(t.suspensionCompression * t.mass, 0)} / ${f(t.suspensionRelaxation * t.mass, 0)} N·s/m (${t.suspensionCompression} / ${t.suspensionRelaxation} × mass)`],
      ['Max suspension force per wheel', `${t.maxSuspensionForce} N`],
      ['Anti-roll bars, roll centre geometry', 'none; lateral tyre force acts at the contact point'],
      ['Tyre friction (Rapier frictionSlip μ)', `front 2.4 − min(0.5, v/60), rear 2.2 − min(0.4, v/65) (v in m/s); handbrake rear 0.65`],
      ['Tyre force model', 'Rapier ray-cast vehicle: lateral impulse solves the contact patch to zero side velocity (× side stiffness 1.1), capped by friction ellipse μ·N with the longitudinal impulse weighted 0.5; no slip curve, no load sensitivity'],
      ['Max road-wheel steer', `${f(deg(t.maxSteer), 1)}° at rest, lock = max / (1 + (v / ${t.steerHalfSpeed} m/s)²): ${f(deg(t.maxSteer / (1 + (50 / 3.6 / t.steerHalfSpeed) ** 2)), 1)}° at 50, ${f(lock60, 1)}° at 60, ${f(deg(t.maxSteer / (1 + (100 / 3.6 / t.steerHalfSpeed) ** 2)), 1)}° at 100 km/h`],
      ['Stick → steer input', 'left stick through a radial dead zone 0.16 and exponent 1.35 (input/bindings.ts), no smoothing; the harness feeds VehicleInput.steer after that curve'],
      ['Steering rate limit', `${t.steerRate} rad/s (${f(deg(t.steerRate), 0)}°/s) at the road wheel; both front wheels same angle (no Ackermann geometry)`],
      ['Drivetrain', `${t.frontTorque === 1 ? 'FWD' : t.frontTorque ? `4WD, ${t.frontTorque * 100} % front` : 'RWD'}; ${t.engineForce} N per driven wheel (${t.engineForce * t.driven.length} N total), no gears or differential; force tapers to 0 over the last 5 m/s below ${t.topSpeed} m/s (${f(t.topSpeed * 3.6, 0)} km/h)`],
      ['Brakes', `${t.brakeForce} N·s impulse per wheel per step = ${f(brakeN, 0)} N per wheel at 60 Hz, all four wheels equal (50/50); no brake ramp`],
      ['Aero drag', `decel = (0.015 + 0.004·v)·v m/s² (equivalent CdA ≈ ${f(0.004 * t.mass / 0.6125, 1)} m²)`],
      ['Assists', `body angular damping ${f(st.angularDamping, 1)} s⁻¹ (all axes); speed-dependent steering lock and rate limit; no ABS, TCS or ESC; no wheel spin DOF (wheels cannot lock)`],
    ]), '');

    md.push('#### Static and ride', '');
    md.push(table(['Quantity', 'Front axle', 'Rear axle', 'Body mode'], [
      ['Corner load (static)', `${f((st.loads[0] + st.loads[1]) / 2, 0)} N`, `${f((st.loads[2] + st.loads[3]) / 2, 0)} N`, `total ${f(st.weight, 0)} N`],
      ['Ride frequency, analytic √(k/m)/2π', `${f(st.corner[0].fn)} Hz`, `${f(st.corner[2].fn)} Hz`, ''],
      ['Damping ratio, analytic bump / rebound', `${f(st.corner[0].zc)} / ${f(st.corner[0].zr)}`, `${f(st.corner[2].zc)} / ${f(st.corner[2].zr)}`, ''],
      ['5 cm heave release (pure heave): fitted fn / ζ of axle compression', `${f(st.frontFit.fn)} Hz / ${f(st.frontFit.zeta)}`, `${f(st.rearFit.fn)} Hz / ${f(st.rearFit.zeta)}`,
        `heave (CoM height): ${f(st.heaveFit.fn)} Hz / ${f(st.heaveFit.zeta)}, overshoot ${f(st.heaveOvershoot * 100, 1)} %`],
      ['Nose 5 cm up at the front axle, released: pitch mode', '', '', `${f(st.pitchFit.fn)} Hz / ζ ${f(st.pitchFit.zeta)}, overshoot ${f(st.pitchOvershoot * 100, 1)} % (start ${f(st.pitchInit, 2)}°)`],
      ['CoM height / chassis box bottom', '', '', `${f(st.comHeight, 3)} m / ${f(st.bellyClearance, 3)} m`],
    ]), '');

    md.push('#### Skidpad (steady state, left turns)', '');
    for (const { r, rows } of pads) {
      md.push(`R = ${r} m`, '');
      md.push(table(['Target km/h', 'Speed km/h', 'R = v/r m', 'Lat g', 'Steer °', 'Ackermann °', 'Lock °', 'Roll °', 'Front slip °', 'Rear slip °', 'Sideslip β °',
        'FL / FR N (in/out)', 'RL / RR N (in/out)', 'Radial err m', 'Held'],
      rows.map(x => [x.target, f(x.speed, 1), f(x.radius, 2), f(x.ay, 3), f(x.steer, 2), f(x.ackermann, 2), f(x.lock, 2), f(x.roll, 2), f(x.alphaF, 2), f(x.alphaR, 2), f(x.beta, 2),
        `${f(x.loads[0], 0)} / ${f(x.loads[1], 0)}`, `${f(x.loads[2], 0)} / ${f(x.loads[3], 0)}`, `${f(x.err, 2)} (max ${f(x.errMax, 2)})`, x.held ? 'yes' : `no: ${x.why}`])), '');
    }
    md.push(table(['Fit', ...fits.map(x => `R = ${x.r} m`)], [
      ['Understeer gradient K, ay < 0.5 g (deg/g)', ...fits.map(x => `${f(x.K.slope, 2)} (n = ${x.K.n})`)],
      ['K over all held steps (deg/g)', ...fits.map(x => `${f(x.Kall.slope, 2)} (n = ${x.Kall.n})`)],
      ['Roll gradient, ay < 0.5 g (deg/g)', ...fits.map(x => `${f(x.roll.slope, 2)}`)],
      ['Roll gradient, all held steps (deg/g)', ...fits.map(x => f(x.rollAll.slope, 2))],
      ['Max steady lateral g (held)', ...fits.map(x => x.max ? `${f(x.max.ay, 3)} at ${f(x.max.speed, 1)} km/h` : 'n/a')],
      ['First step not held', ...fits.map(x => x.fail ? `${x.fail.target} km/h: ${x.fail.why}` : 'none')],
      ['Lateral load transfer arm (m), mean of held steps', ...fits.map(x => `${f(x.lltArm, 3)} (CoM height ${f(st.comHeight, 3)})`)],
      ['Lateral load transfer on the front axle (%)', ...fits.map(x => f(x.lltdFront * 100, 1))],
    ]), '');
    md.push('Full stick held, PI speed hold (largest steady lateral g the speed-dependent lock allows)', '');
    md.push(table(['Target km/h', 'Speed km/h', 'R = v/r m', 'Lat g', 'Steer °', 'Kinematic L·r/v °', 'Front / rear slip °', 'Sideslip β °', 'Roll °', 'Throttle', 'Rear slip ratio est.', 'Steady'],
      stick.map(x => [x.target, f(x.speed, 1), f(x.radius, 2), f(x.ay, 3), f(x.steer, 2), f(x.ackermann, 2), `${f(x.alphaF, 2)} / ${f(x.alphaR, 2)}`, f(x.beta, 2), f(x.roll, 2),
        f(x.throttle, 3), f(x.kappaR, 3), x.steady ? 'yes' : 'no'])), '');

    md.push('#### Straight-line braking (full brake, steering straight)', '');
    md.push(table(['Start km/h', 'Mean decel 90→10 % (g)', 'Peak decel g (raw / 5-step)', 'Brake → 90 % peak s', 'Stop distance m', 'Stop time s', 'Pitch nose down ° (mean / peak)',
      'Front load share braking / static', 'Fx front / rear N', 'Aero drag at start g', 'Slip ratio est. front / rear', 'Rear wheel lifts', 'Start'],
    brakes.map(x => [x.kmh, f(x.meanDecel, 3), `${f(x.peak, 3)} / ${f(x.peakSmooth, 3)}`, f(x.t90peak, 3), f(x.stopDist, 2), f(x.stopTime, 2), `${f(x.pitch, 2)} / ${f(x.peakPitch, 2)}`,
      `${f(x.frontShare * 100, 1)} % / ${f(x.staticShare * 100, 1)} %`, `${f(x.fxF, 0)} / ${f(x.fxR, 0)}`, f(x.dragG, 3), `${f(x.kappaF, 3)} / ${f(x.kappaR, 3)}`,
      x.rearLift ? 'yes' : 'no', x.injected ? `injected (top ${f(x.plateau, 1)} km/h)` : 'driven'])), '');

    md.push('#### Drivability checks', '');
    md.push(table(['Check', 'Result', 'OK'], checks.map(c => [c.name, c.value, c.ok ? 'yes' : '**no**'])), '');
    md.push('#### Step steer at 60 km/h (throttle held at the 60 km/h value)', '');
    md.push(table(['Road-wheel step', 'Steer time to 90 / 100 % s', 'Steady yaw °/s', 'Steady lat g', 'Yaw 10–90 % rise s', 'Steer50 → yaw 10 / 90 % s', 'Steer50 → peak s',
      'Overshoot %', 'Settle ±5 % s', 'Yaw rate / kinematic v·δ/L', 'Front / rear slip °', 'Sideslip β °', 'Roll °', 'Throttle', 'Speed at step / 2–3 s / 4 s km/h'],
    steps.map(x => [x.stepDeg === 'lock' ? `full stick (${f(x.lock0, 2)}° lock at 60)` : x.stepDeg === 'key' ? `key held (${f(x.lock0, 2)}° lock at 60)` : `${x.stepDeg}°`, `${f(x.steer90, 3)} / ${f(x.steer100, 3)}`, f(x.yawSS, 2), f(x.aySS, 3),
      f(x.rise, 3), `${f(x.d10, 3)} / ${f(x.d90, 3)}`, f(x.tPeak, 3), f(x.overshoot, 1), f(x.settle, 3), f(x.kinematicRatio, 3), `${f(x.alphaF, 2)} / ${f(x.alphaR, 2)}`,
      f(x.beta, 2), f(x.roll, 2), f(x.throttle, 3),
      `${f(x.speed0, 1)} / ${f(x.speedWin, 1)} / ${f(x.speed4, 1)}`])), '');
  }
  fs.mkdirSync(RUNS, { recursive: true });
  fs.writeFileSync(path.join(RUNS, 'summary.json'), JSON.stringify({ commit, branch, dirty, ...out }, (_, v) => typeof v === 'number' ? +v.toFixed(5) : v, 1));
  const header = [
    '## Turku3D baseline: headless Rapier handling runs',
    '',
    `Branch \`${branch}\`, harness commit \`${commit}\`${dirty ? ' (uncommitted harness changes)' : ''}, file \`game/tests/unit/wf2-compare.harness.test.ts\`. `
    + 'Run with `WF2_COMPARE=1 npx vitest run tests/unit/wf2-compare.harness.test.ts` in `game/`. Raw per-step CSV logs: '
    + `\`${RUNS}\`.`,
    '',
    'Method. Each run builds a `PhysicsWorld` with one flat 8 km road slab (solid tile collision groups) and the player vehicle, and advances it with '
    + '`PhysicsWorld.fixedStep` at the fixed 1/60 s step: the same `Vehicle.step` → `world.step` → `afterStep` sequence the physics worker runs, '
    + 'with `VehicleInput` throttle/brake/steer values like the gamepad. Steering goes through the game\'s speed-dependent lock and 2.5 rad/s rate limit; '
    + 'the harness inverts the lock each step to ask for a road-wheel angle and logs the actual angle. Accelerations are finite differences of the '
    + 'centre-of-mass velocity projected on the horizontal heading frame (no gravity component), yaw rate is the heading change per step (Rapier\'s angvel() reads 2 % low after its angular damping), roll and pitch '
    + 'come from the body axes. Wheel loads are Rapier suspension forces, slip angles are computed from the contact-point velocity and the wheel heading, '
    + 'slip ratio is the game\'s own sliding estimate (`tyreSlip.ts`), since the ray-cast vehicle has no wheel spin. Skidpad: pure pursuit from the rear '
    + 'axle (lookahead 0.5·v, 4–12 m) plus an integral on radial error, PI speed hold, left turns; each speed held 6 s (10 s for the first), '
    + 'averages over the last 2 s; "held" means speed within 1 km/h, mean radial error ≤ 0.5 m and peak ≤ 1 m. Understeer gradient: steer − wheelbase/(v/r) '
    + 'against lateral g. Full stick (extra, to find the largest steady lateral g): full left stick with the same speed hold and averaging. Braking: PI cruise until the speed is held 3 s, then brake = 1, throttle 0, steer 0 to standstill; speeds above the reachable '
    + 'top speed start from an injected chassis velocity. Step steer: PI cruise at 60 km/h for 4 s, then the throttle is frozen at its mean over the last '
    + 'second and the road-wheel angle is commanded as a step and held; steady values are the mean over 2–3 s after the command. Ride: the settled car is '
    + 'released 5 cm higher (and, separately, with the nose 5 cm up at the front axle) with zero velocity, and a damped second-order response is fitted.',
    '',
  ];
  fs.mkdirSync(path.dirname(REPORT), { recursive: true });
  const col = (k: string) => KINDS.map(kind => notes[kind][k]);
  const noteTable = ['### Reliability notes', '', table(['Issue', 'Effect on the metrics', ...KINDS.map(k => LABEL[k].split(' (')[0])], [
    ['Skidpad limit is the steering lock, not grip', 'the speed-dependent lock falls below the Ackermann angle at these speeds (R 20 / R 30); the circle is lost there with tyres far from saturation, so "max steady lateral g" on the circles is lock-limited', ...col('lock')],
    ['Largest steady lateral g anywhere', 'full stick with a speed hold (table under each skidpad); above it the inner front tyre saturates and the car understeers', ...col('grip')],
    ['No tyre slip curve', 'Rapier solves each contact patch to zero side velocity until the friction ellipse saturates; the remaining slip angles (about 0.6°/g front, 0.5°/g rear) are solver residue, so K ≈ 0 deg/g and slip angles are not comparable with a slip-curve tyre model', '', ''],
    ['Lateral load transfer arm', 'measured from inner/outer loads: side impulses act only about a tenth of the CoM height below the CoM (consistent with a Bullet-style roll influence ≈ 0.1, not exposed in the JS API), so roll gradient and inner/outer load split are about a tenth of the geometric value; longitudinal forces use the full height (front share in braking)', ...col('roll')],
    ['Top speed', 'engine taper plus quadratic drag (≈10× a real car\'s drag); drag adds 0.10–0.36 g to braking at the start speeds', ...col('top')],
    ['Braking profile', 'full brake force in one 16.7 ms step (no pressure ramp), so "time to 90 % of peak" is one step; the peak is that first step before pitch unloads the rear; no ABS and no wheel spin DOF, so wheels cannot lock; slip ratio is the game\'s tyre-mark estimate', ...col('brake')],
    ['Static compression', 'the chassis keeps a constant ≈0.06 m/s closing velocity at rest (consistent with Rapier\'s 4 solver substeps), so the damper carries ≈12 % of the static load: static compression is smaller than load/k; heave frequency is unaffected', ...col('sag')],
    ['Pitch mode', 'near critical damping (spring dampers plus 1.2 s⁻¹ body angular damping), so its fitted frequency is poorly determined', '', ''],
    ['Step steer timing', 'yaw follows the road-wheel angle almost kinematically, so yaw passes 10 % before the steer reaches 50 % (negative delay); with full stick the lock opens as speed drops, so yaw keeps rising and peak/overshoot are not meaningful for that row', ...col('lockStep')],
    ['Sleep', `the harness wakes the chassis whenever Rapier puts it to sleep; highest speed at which that happened: ${f(WAKE_SPEED.max * 3.6, 2)} km/h (rest phases only)`, '', ''],
  ]), ''];
  fs.writeFileSync(REPORT, [...header, ...noteTable, ...md].join('\n'));
}, 900_000);
