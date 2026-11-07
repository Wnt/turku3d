// Path followers for the autopilot. They turn a pose and a path into the same inputs a controller produces:
// throttle, brake, steer and handbrake for a vehicle, a world-space stick direction for walking. Nothing here
// touches physics; the inputs go through the normal input path (input/index.ts driver).
//
// Driving: pure pursuit from the rear axle (lookahead grows with speed) plus a small cross-track term, a speed
// plan from path curvature (lateral acceleration limit) and the stop at the end (braking limit), and a stuck
// recovery that reverses and asks for a re-plan.
import { curvatureAt, project, sampleAt, type Path, type PathSample } from './path';
import { effectiveWheelbase, type RearSteerTuning } from '../../vehicle/rearSteer';

export interface VehiclePose {
  x: number; y: number; z: number;
  /** Unit horizontal chassis forward (engine x, z). */
  hx: number; hz: number;
  /** Signed speed along the chassis forward (m/s). */
  forwardSpeed: number;
}

export interface VehicleGeometry {
  /** Front to rear axle (m). */
  wheelbase: number;
  /** Rear axle behind the chassis origin (m). */
  rearOffset: number;
  maxSteer: number;
  steerHalfSpeed: number;
  /** A 4WS vehicle in its passive AUTO mode turns like a shorter car (SPEC §3.2). */
  rearSteer?: RearSteerTuning;
  /** Chassis origin to the rear end (m) and half width (m): the reverse-out ground check (reverseCheck.ts). */
  back?: number;
  halfWidth?: number;
}

export type DriveStyle = 'calm' | 'normal' | 'fast';
const STYLE: Record<DriveStyle, { lateral: number; brake: number; accel: number }> = {
  // Comfortable passenger limits (m/s²) and the brisker GTA-taxi style.
  calm: { lateral: 1.8, brake: 2.0, accel: 1.5 },
  normal: { lateral: 2.6, brake: 2.8, accel: 2.5 },
  fast: { lateral: 4.5, brake: 4.5, accel: 4 },
};

export interface DriveCommand { throttle: number; brake: number; steer: number; handbrake: boolean }

export interface DriveOptions {
  /** Cruise speed (m/s); the path's own limit applies on top when given. */
  speed: number;
  style?: DriveStyle;
  /** Stop at the path end (true) or keep cruising through it (false: a chained path follows). */
  stop?: boolean;
  /** Arrival radius (m). */
  arrive?: number;
  /** Per-arc-length speed limit (m/s), e.g. the lanes' own limits. */
  limit?: (s: number) => number;
  /** The caller checks the ground behind before each reverse-out (reverseCheck.ts) and starts it with reverse(), or
   *  ends the trip; the car holds meanwhile. Without it the follower backs out at once. */
  checkReverse?: boolean;
}

export type FollowStatus = 'driving' | 'arrived' | 'stuck' | 'off-path' | 'recovering';

export interface DriveStep { command: DriveCommand; status: FollowStatus; s: number; lateral: number; target: number }

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Seconds of no progress while asking for throttle before the follower reverses out. */
const STUCK_AFTER = 2.2;
/** Reverse for this long (s) or this far (m), steering the other way. */
const REVERSE_TIME = 1.8, REVERSE_M = 4;
/** A car farther than this from its path has left it: the caller re-plans. */
const OFF_PATH_M = 9;

export class DriveFollower {
  s = 0;
  private stuckFor = 0;
  private reverseFor = 0;
  private reverseFrom: { x: number; z: number } | null = null;
  private reverseSteer = 0;
  private lastPose: VehiclePose | null = null;
  /** The steer of a reverse-out waiting for the caller's ground check (options.checkReverse), else null. */
  pendingReverse: number | null = null;
  recoveries = 0;
  private readonly sample: PathSample = { x: 0, z: 0, y: null, hx: 1, hz: 0 };

  constructor(public path: Path, readonly geometry: VehicleGeometry, public options: DriveOptions) {}

  /** Swap in a re-planned path; progress restarts from the nearest point. */
  setPath(path: Path) { this.path = path; this.s = 0; this.s = this.locate(null).s; this.stuckFor = 0; }

  private locate(pose: VehiclePose | null) {
    if (!pose) return { s: 0, distance: 0, x: 0, z: 0, lateral: 0 };
    // Search near the last progress first so a looping or overlapping path never jumps ahead.
    const near = project(this.path, pose.x, pose.z, Math.max(0, this.s - 15), this.s + 40, pose.y);
    return near.distance < 6 ? near : project(this.path, pose.x, pose.z, 0, Infinity, pose.y);
  }

  /** Target speed at arc length s: cruise, curvature and the stop ahead. */
  plannedSpeed(s: number, v: number): number {
    const style = STYLE[this.options.style ?? 'normal'], path = this.path;
    let target = this.options.speed;
    const horizon = v * v / (2 * style.brake) + 25;
    for (let d = 0; d <= horizon && s + d <= path.length; d += 2) {
      const k = Math.abs(curvatureAt(path, s + d, 4));
      const limit = Math.min(this.options.speed, k > 1e-4 ? Math.sqrt(style.lateral / k) : Infinity, this.options.limit?.(s + d) ?? Infinity);
      target = Math.min(target, Math.sqrt(limit * limit + 2 * style.brake * d));
    }
    if (this.options.stop !== false) {
      const remain = Math.max(0, path.length - s - 1.5);
      target = Math.min(target, Math.sqrt(2 * style.brake * 0.8 * remain));
    }
    return Math.max(0, target);
  }

  /** Starts the reverse-out with `steer` (after the caller's ground check). */
  reverse(steer: number) {
    this.pendingReverse = null; this.stuckFor = 0;
    this.reverseFor = REVERSE_TIME; this.reverseSteer = steer;
    this.reverseFrom = this.lastPose ? { x: this.lastPose.x, z: this.lastPose.z } : null;
  }

  step(pose: VehiclePose, dt: number): DriveStep {
    const g = this.geometry, path = this.path;
    this.lastPose = pose;
    const at = this.locate(pose);
    const v = pose.forwardSpeed, speed = Math.abs(v);
    const progressed = at.s - this.s;
    this.s = Math.max(this.s, at.s);
    const remain = path.length - this.s;
    const hold = { throttle: 0, brake: 0, steer: 0, handbrake: true };
    if (this.options.stop !== false && remain < (this.options.arrive ?? 2.5) && speed < 0.8) {
      return { command: hold, status: 'arrived', s: this.s, lateral: at.lateral, target: 0 };
    }
    if (at.distance > OFF_PATH_M) return { command: hold, status: 'off-path', s: this.s, lateral: at.lateral, target: 0 };

    // Waiting for the ground check behind before a reverse-out: hold still.
    if (this.pendingReverse !== null) return { command: hold, status: 'recovering', s: this.s, lateral: at.lateral, target: 0 };
    // Reverse-out manoeuvre after being stuck: back up with opposite lock, then resume.
    if (this.reverseFor > 0) {
      this.reverseFor -= dt;
      const moved = this.reverseFrom ? Math.hypot(pose.x - this.reverseFrom.x, pose.z - this.reverseFrom.z) : 0;
      if (this.reverseFor <= 0 || moved > REVERSE_M) { this.reverseFor = 0; this.stuckFor = 0; }
      // LT reverses once stopped (vehicle/handling.ts): the brake pedal is the reverse throttle.
      return { command: { throttle: 0, brake: 0.7, steer: this.reverseSteer, handbrake: false }, status: 'recovering',
        s: this.s, lateral: at.lateral, target: -1 };
    }

    // Pure pursuit from the rear axle toward a point `lookahead` metres along the path.
    const rx = pose.x - pose.hx * g.rearOffset, rz = pose.z - pose.hz * g.rearOffset;
    const lookahead = clamp(3.5 + 0.55 * speed, 4, 18);
    const aim = sampleAt(path, Math.min(path.length + 30, this.s + lookahead + g.rearOffset), this.sample);
    // Past the end, aim along the final heading so the car does not swerve onto the end point.
    const over = this.s + lookahead + g.rearOffset - path.length;
    const ax = (over > 0 ? aim.x + aim.hx * over : aim.x) - rx, az = (over > 0 ? aim.z + aim.hz * over : aim.z) - rz;
    const ld = Math.hypot(ax, az) || 1;
    // Right of the chassis heading (x east, z south): (-hz, hx).
    const sinAlpha = (ax * -pose.hz + az * pose.hx) / ld;
    const wheelbase = g.rearSteer ? effectiveWheelbase(g.rearSteer, g.wheelbase, g.maxSteer, speed) : g.wheelbase;
    let wheel = Math.atan(2 * wheelbase * sinAlpha / ld);
    // Cross-track damping (Stanley-like): pulls the car onto the lane line at low speed and on long curves.
    wheel += Math.atan(0.35 * -at.lateral / (speed + 2)) * 0.5;
    const lock = g.maxSteer / (1 + (speed / g.steerHalfSpeed) ** 2);
    const steer = clamp(wheel / lock, -1, 1);

    const target = this.plannedSpeed(this.s, speed);
    let throttle = 0, brake = 0;
    const error = target - v;
    if (error > 0) throttle = clamp(0.12 + error * 0.35 + (target > 1 ? 0.08 : 0), 0, 1);
    else if (error < -0.6) brake = clamp(-error * 0.3, 0.05, 1);
    // A car rolling backwards when it should go forward: drive, never the reversing brake pedal.
    if (v < -0.3 && target > 0) { brake = 0; throttle = 1; }
    // Never press the brake at a standstill: at walking pace LT is the reverse throttle (vehicle/handling.ts).
    if (brake > 0 && v < 0.6) { brake = 0; throttle = 0; }
    const handbrake = target < 0.3 && speed < 0.8;

    // Stuck: throttle held, no progress along the path.
    if (throttle > 0.3 && speed < 0.5 && progressed < 0.02) this.stuckFor += dt; else if (speed > 1) this.stuckFor = 0;
    if (this.stuckFor > STUCK_AFTER) {
      this.recoveries++;
      this.stuckFor = 0;
      const away = -Math.sign(steer || 1);
      if (this.options.checkReverse) this.pendingReverse = away; else this.reverse(away);
      return { command: { throttle: 0, brake: 0, steer: 0, handbrake: false }, status: 'stuck', s: this.s, lateral: at.lateral, target };
    }
    return { command: { throttle, brake, steer, handbrake }, status: 'driving', s: this.s, lateral: at.lateral, target };
  }
}

export interface WalkPose { x: number; y: number; z: number }
export interface WalkStep { dx: number; dz: number; sprint: boolean; walk: boolean; jump: boolean; status: FollowStatus; s: number; lateral: number }

/** Walking: steer the stick toward a point a couple of metres ahead; jump once when held up, then report stuck. */
export class WalkFollower {
  s = 0;
  private stuckFor = 0;
  private lastAt: { x: number; z: number } | null = null;
  recoveries = 0;
  private jumped = false;
  private readonly sample: PathSample = { x: 0, z: 0, y: null, hx: 1, hz: 0 };

  constructor(public path: Path, public options: { pace: 'walk' | 'run' | 'sprint'; arrive?: number }) {}

  setPath(path: Path) { this.path = path; this.s = 0; this.stuckFor = 0; }

  step(pose: WalkPose, dt: number): WalkStep {
    const near = project(this.path, pose.x, pose.z, Math.max(0, this.s - 5), this.s + 20, pose.y, 3);
    const at = near.distance < 5 ? near : project(this.path, pose.x, pose.z, 0, Infinity, pose.y, 3);
    this.s = Math.max(this.s, at.s);
    const base = { sprint: false, walk: false, jump: false, s: this.s, lateral: at.lateral };
    const end = sampleAt(this.path, this.path.length);
    if (Math.hypot(end.x - pose.x, end.z - pose.z) < (this.options.arrive ?? 1.2)) return { ...base, dx: 0, dz: 0, status: 'arrived' };
    if (at.distance > OFF_PATH_M) return { ...base, dx: 0, dz: 0, status: 'off-path' };
    const aim = sampleAt(this.path, Math.min(this.path.length, this.s + 2.2), this.sample);
    let dx = aim.x - pose.x, dz = aim.z - pose.z;
    const d = Math.hypot(dx, dz) || 1;
    dx /= d; dz /= d;
    const moved = this.lastAt ? Math.hypot(pose.x - this.lastAt.x, pose.z - this.lastAt.z) : 1;
    this.lastAt = { x: pose.x, z: pose.z };
    if (moved < 0.3 * dt) this.stuckFor += dt; else this.stuckFor = Math.max(0, this.stuckFor - dt);
    let jump = false, status: FollowStatus = 'driving';
    if (this.stuckFor > 1.2 && !this.jumped) { jump = true; this.jumped = true; this.recoveries++; }
    if (this.stuckFor > 4) { status = 'stuck'; this.stuckFor = 0; this.jumped = false; }
    const slow = this.path.length - this.s < 3;
    return { ...base, dx, dz, jump, status, sprint: this.options.pace === 'sprint' && !slow, walk: this.options.pace === 'walk' || slow };
  }
}
