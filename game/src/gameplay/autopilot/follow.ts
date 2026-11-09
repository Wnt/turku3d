// Path followers for the autopilot. They turn a pose and a path into the same inputs a controller produces:
// throttle, brake, steer and handbrake for a vehicle, a world-space stick direction for walking. Nothing here
// touches physics; the inputs go through the normal input path (input/index.ts driver).
//
// Driving: pure pursuit from the rear axle (lookahead grows with speed) plus a small cross-track term, a speed
// plan from path curvature (lateral acceleration limit) and the stop at the end (braking limit), and a stuck
// recovery that reverses and asks for a re-plan. With obstacles (other cars) it keeps a gap behind what it follows,
// stops for what stands or crosses in its way, and passes a still car that leaves room beside it (avoidObstacles).
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

/** A car or other body near the path (engine x, z; unit heading; half extents in metres; velocity in m/s). */
export interface Obstacle {
  x: number; z: number; hx: number; hz: number; halfLength: number; halfWidth: number; vx: number; vz: number;
  /** Ambient lane traffic: followed and queued behind, never passed (a still one waits at a light or in a queue). */
  traffic: boolean;
}

/** Room kept beside the car (m), and the gap behind what it follows: standstill (m) plus a time gap (s). */
const SIDE_CLEAR = 0.35, GAP_STILL = 3.5, GAP_TIME = 1.1;
/** Moving obstacles are also checked where they will be after these times (s): a car crossing the path ahead. A
 *  predicted place counts only when the car reaches it within PREDICT_MATCH s of that time. */
const PREDICT = [0, 0.7, 1.4, 2.1, 2.8, 3.5], PREDICT_MATCH = 1.2;
/** Obstacles count along the path up to the braking distance plus this (m), at most LOOK_MAX. */
const LOOK_MARGIN = 20, LOOK_MAX = 90;
/** A still obstacle (not lane traffic) within NUDGE_AHEAD metres is passed with the car shifted at most NUDGE_MAX
 *  metres off its path, toward the side that needs the smaller shift, at NUDGE_RATE m/s (more when faster). */
const NUDGE_AHEAD = 30, NUDGE_MAX = 2.3, NUDGE_RATE = 0.9;
/** Speeds (m/s) below which an obstacle counts as still. */
const STILL = 0.7;
/** Walls and posts (static collision) along the corridor ahead: rays WALL_HEIGHT m above the road along the path in
 *  WALL_STEP m chords, at the corridor's centre and both edges, as far as the braking distance plus WALL_MARGIN.
 *  A hit with a near-vertical face (|normal y| < WALL_NORMAL_Y) counts; the car stops WALL_GAP m short of it. A
 *  result older than WALL_FRESH s is dropped. Held by one for WALL_PATIENCE s (a lane drawn through a wall), the car
 *  starts the stuck recovery: it backs out and re-plans, and gives up like any stuck trip. */
export const WALL_PERIOD = 0.1;
const WALL_HEIGHT = 0.75, WALL_STEP = 4, WALL_MARGIN = 12, WALL_MAX = 60, WALL_NORMAL_Y = 0.6, WALL_GAP = 1.2, WALL_FRESH = 0.6,
  WALL_PATIENCE = 3;

export interface WallRay { origin: { x: number; y: number; z: number }; dir: { x: number; y: number; z: number }; length: number; s: number }

/** The rays that look for walls along the path ahead of a car at arc length `s` driving `nudge` m right of it. */
export function wallRays(path: Path, s: number, nudge: number, geometry: VehicleGeometry, pose: VehiclePose, brake = STYLE.normal.brake): WallRay[] {
  const speed = Math.abs(pose.forwardSpeed), half = geometry.back ?? 2.3, w = (geometry.halfWidth ?? 0.95) + 0.1;
  const reach = Math.min(WALL_MAX, speed * speed / (2 * brake) + WALL_MARGIN), from = s + half, to = Math.min(path.length, from + reach);
  const rays: WallRay[] = [], a: PathSample = { x: 0, z: 0, y: null, hx: 1, hz: 0 }, b = { ...a };
  for (let u = from; u < to - 0.5; u += WALL_STEP) {
    const v = Math.min(to, u + WALL_STEP);
    sampleAt(path, u, a); sampleAt(path, v, b);
    const ya = (a.y ?? pose.y - 0.6) + WALL_HEIGHT, yb = (b.y ?? pose.y - 0.6) + WALL_HEIGHT;
    for (const off of [-w, 0, w]) {
      const l = nudge + off;
      const o = { x: a.x - a.hz * l, y: ya, z: a.z + a.hx * l }, e = { x: b.x - b.hz * l, y: yb, z: b.z + b.hx * l };
      const dx = e.x - o.x, dy = e.y - o.y, dz = e.z - o.z, length = Math.hypot(dx, dy, dz);
      if (length > 0.1) rays.push({ origin: o, dir: { x: dx / length, y: dy / length, z: dz / length }, length, s: u });
    }
  }
  return rays;
}

/** The nearest wall along the path from the rays' hits (null: none), for DriveFollower.wallAhead. */
export function wallHit(rays: readonly WallRay[], hits: readonly ({ toi: number; normal: { y: number } } | null)[]): number | null {
  let best: number | null = null;
  rays.forEach((r, i) => {
    const h = hits[i];
    if (h && Math.abs(h.normal.y) < WALL_NORMAL_Y && (best === null || r.s + h.toi < best)) best = r.s + h.toi;
  });
  return best;
}

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
/** A turn in the street starts when the path's aim point lies this far off the car's heading (cosine) at a
 *  standstill: behind it, or so far to the side that full lock from rest swings wide (off an Amsterdam quay into the
 *  canal at a leg start). It ends once the car faces within TURN_DONE_COS of the aim. */
const TURN_START_COS = 0.3, TURN_DONE_COS = 0.6;
/** ...and once its heading is within this (cosine) of the path's own heading at the aim. */
const TURN_DONE_ALONG = 0.6;
/** Pursuit with the aim this far off the heading (cosine) drives no faster than SIDE_AIM_SPEED (m/s). */
const SIDE_AIM_COS = 0.5, SIDE_AIM_SPEED = 3;
/** Forward legs of a turn before it gives up (the trip ends stuck), and turns per path. */
const TURN_ROUNDS = 4, TURN_TRIES = 6;
/** A hairpin in the path: its heading turns by more than HAIRPIN_COS (cosine) within HAIRPIN_M metres, tighter than
 *  any car turns (a lane graph's U-turn at a dead end, 1.7 m radius between two lanes). The car stops before it and
 *  turns in the street. */
const HAIRPIN_M = 8, HAIRPIN_COS = -0.5;
/** A hairpin turn aims far along the way out, so it ends only once the car faces that well (cosine). */
const HAIRPIN_DONE_COS = 0.85;
/** Speed (m/s) in a turn's legs, and a leg's longest travel (m) and time (s). */
const TURN_SPEED = 1.2, TURN_LEG_S = 6;
export const TURN_LEG_M = 4.5;
/** A leg ends after this long (s) without moving, once it has run a second. After TURN_PUSH_S still it presses
 *  harder (TURN_PUSH): a wheel against a kerb or a tree pit's edge does not climb at walking-pace pedal (Amsterdam
 *  Warmoesstraat: a reverse leg stood still on the tree strip and the turn counted it blocked). */
const TURN_STALL_S = 1.2, TURN_PUSH_S = 0.3, TURN_PEDAL = 0.4, TURN_PUSH = 0.8;
/** A turn's legs swap their lock when the way out lies this far (sine) to the other side. */
const TURN_FLIP_SIN = 0.25;
/** A turn leg that takes the car this far (m) from its path, or half a metre beyond where the leg started if that was
 *  further, ends there and the next leg comes back; a turn beyond OFF_PATH_M - 1 gives up. (Amsterdam Warmoesstraat,
 *  Rotterdam Botersloot: a leg rolled past 6 m, or a back-up left the car 7 m off, and the turn gave up at once.) */
const TURN_AWAY_M = 6;
/** A turn whose legs got nowhere first backs straight up this far (m, as far as the ground allows) and starts again
 *  there, once: a car nosed into a 5-6 m street a few metres past a junction turns in the junction. */
export const TURN_BACKUP_M = 8;
/** Below this speed (m/s), with the wheel turned beyond EDGE_WATCH_STEER, pursuit has the caller check the ground
 *  along its arc ahead (reverseCheck.ts edgeRoom) every EDGE_WATCH_S: a slow full-lock swing is how cars went over
 *  quay edges into canals. An edge within the stopping distance stops the car, and it turns in the street instead;
 *  the third such stop gives the trip up. */
const EDGE_WATCH_SPEED = 4, EDGE_WATCH_STEER = 0.6, EDGE_WATCH_S = 0.2, EDGE_STOPS = 3;
/** A turn's candidate first legs: rotation toward the aim's side (1) or the long way round (-1), and direction. The
 *  steer of a leg is its side forward and the opposite lock back. */
export const TURN_FIRST_LEGS: [rotation: 1 | -1, dir: 1 | -1][] = [[1, 1], [1, -1], [-1, 1], [-1, -1]];

export class DriveFollower {
  s = 0;
  private stuckFor = 0;
  private reverseFor = 0;
  private reverseFrom: { x: number; z: number } | null = null;
  private reverseSteer = 0;
  private lastPose: VehiclePose | null = null;
  /** The steer of a reverse-out waiting for the caller's ground check (options.checkReverse), else null. */
  pendingReverse: number | null = null;
  /** A turn leg waiting for the caller's ground check (options.checkReverse: turnLegChecked with the metres it may
   *  drive), else null. */
  pendingTurnLeg: { steer: number; dir: 1 | -1; length: number; pick: boolean; aimSide: number } | null = null;
  /** A turn in the street (the path leaves behind the car: a U-turn, a dead end): forward with the lock toward the
   *  path, back with the other, at walking pace, each leg only as far as the carriageway allows, until the car faces
   *  the path. Not a stuck recovery unless it gives up. */
  private turn: { side: number; dir: 1 | -1; rounds: number; room: number; from: { x: number; z: number };
    time: number; still: number; blocked: number; aimS: number | null; backup: 'no' | 'driving' | 'done';
    /** Started by the edge watch: it ends only after a leg has turned the car (pursuit's arc led over an edge). */
    edge: boolean; legs: number;
    /** The first leg's direction and side are picked by room (turnLegChecked); later legs alternate. */
    picked: boolean;
    /** How far from the path (m) this leg may take the car (TURN_AWAY_M). */
    away: number } | null = null;
  /** Where the running turn's aim lies (cosine and sine off the heading, + right) when it started or restarted. */
  private aimCos = 1;
  private aimSin = 0;
  /** The edge watch's ground check of the arc ahead (options.checkReverse: edgeChecked), else null. */
  pendingEdgeCheck: { steer: number; length: number } | null = null;
  private edgeAhead: { room: number; steer: number; age: number } | null = null;
  private edgeStops = 0;
  /** Why the follower gave the trip up (a turn in the street with no room), else null: the caller ends the trip. */
  gaveUp: string | null = null;
  private hairpins: number[] | null = null;
  private hairpinsDone = new Set<number>();
  /** Turns in the street started so far (tests, the drive harness). */
  turns = 0;
  recoveries = 0;
  private readonly sample: PathSample = { x: 0, z: 0, y: null, hx: 1, hz: 0 };
  /** Metres right of the path the car drives now to pass a still obstacle (avoidObstacles), and the shift wanted. */
  nudge = 0;
  private nudgeWant = 0;
  /** Gap (m) to the obstacle that limits the speed this step, else null (traces, the bots). */
  obstacleGap: number | null = null;
  /** The nearest wall ahead (arc length, null: none) from the caller's rays (wallRays/wallHit), and its age (s). */
  private wall: { s: number | null; age: number } | null = null;
  private wallStill = 0;
  private wallBlocked = false;
  private wallGap = 0;

  constructor(public path: Path, readonly geometry: VehicleGeometry, public options: DriveOptions) {}

  /** Swap in a re-planned path; progress restarts from the nearest point. */
  setPath(path: Path) {
    this.path = path; this.s = 0; this.s = this.locate(null).s; this.stuckFor = 0; this.turn = null; this.pendingTurnLeg = null;
    this.pendingEdgeCheck = null; this.edgeAhead = null;
    this.hairpins = null; this.hairpinsDone.clear();
    this.nudge = 0; this.nudgeWant = 0; this.wall = null;
  }

  /** The caller's wall rays for the path ahead found the nearest wall at arc length `s` (null: none). */
  wallAhead(s: number | null) { this.wall = { s, age: 0 }; }

  /** The speed (m/s) a wall ahead allows, Infinity when none. */
  private wallSpeed(speed: number, dt: number): number {
    const w = this.wall;
    if (w) w.age += dt;
    if (!w || w.s === null || w.age > WALL_FRESH) { this.wallStill = 0; return Infinity; }
    this.wallGap = Math.round((w.s - this.s) * 10) / 10;
    const gap = w.s - this.s - (this.geometry.back ?? 2.3) - WALL_GAP, brake = STYLE[this.options.style ?? 'normal'].brake;
    const allowed = Math.sqrt(2 * brake * 0.9 * Math.max(0, gap));
    this.wallStill = allowed < 0.3 && speed < 0.3 ? this.wallStill + dt : 0;
    if (this.wallStill > WALL_PATIENCE) { this.wallBlocked = true; this.wallStill = 0; this.wall = null; }
    return allowed;
  }

  /** The speed (m/s) the obstacles near the path ahead allow, and the shift off the path that passes the still ones.
   *  Each obstacle (and a moving one where it will be shortly) is located along the path: its footprint across the
   *  path against the car's corridor (half width plus SIDE_CLEAR, around the shift), its gap ahead bumper to bumper. */
  avoidObstacles(pose: VehiclePose, speed: number, dt: number, obstacles: readonly Obstacle[]): number {
    const g = this.geometry, path = this.path, style = STYLE[this.options.style ?? 'normal'];
    const half = g.back ?? 2.3, w = (g.halfWidth ?? 0.95) + SIDE_CLEAR;
    const reach = Math.min(LOOK_MAX, speed * speed / (2 * style.brake) + LOOK_MARGIN + speed);
    const found: { gap: number; lo: number; hi: number; along: number; still: boolean; predicted: boolean }[] = [];
    const at = this.sample;
    for (const o of obstacles) {
      const dx = o.x - pose.x, dz = o.z - pose.z;
      if (Math.hypot(dx, dz) > reach + 12 || dx * pose.hx + dz * pose.hz < -half - 4) continue;
      const v = Math.hypot(o.vx, o.vz), still = v < STILL;
      for (const t of still ? [0] : PREDICT) {
        const px = o.x + o.vx * t, pz = o.z + o.vz * t;
        const p = project(path, px, pz, Math.max(0, this.s - half - 2), Math.min(path.length + 30, this.s + reach));
        if (p.distance > NUDGE_MAX + w + 4) continue;
        sampleAt(path, p.s, at);
        const cos = Math.abs(o.hx * at.hx + o.hz * at.hz), sin = Math.abs(o.hx * at.hz - o.hz * at.hx);
        const across = o.halfWidth * cos + o.halfLength * sin, along = o.halfLength * cos + o.halfWidth * sin;
        // Past the path's end the projection clamps: the distance beyond it counts along the final heading.
        const beyond = p.s >= path.length - 0.01 ? Math.max(0, (px - at.x) * at.hx + (pz - at.z) * at.hz) : 0;
        const gap = p.s + beyond - this.s - half - along;
        // Kept until the car's rear has passed its front: one beside the car is still passed, never braked for.
        if (gap < -2 * (half + along)) continue;
        if (t > 0 && Math.abs(gap / Math.max(speed, 2) - t) > PREDICT_MATCH) continue;
        found.push({ gap, lo: p.lateral - across, hi: p.lateral + across, along: t > 0 ? 0 : o.vx * at.hx + o.vz * at.hz,
          still: still && !o.traffic, predicted: t > 0 });
      }
    }
    // The smallest shift (staying near the current one) that clears every still obstacle close ahead.
    const passing = found.filter(f => f.still && f.gap < NUDGE_AHEAD);
    const clear = (n: number) => passing.every(f => n + w <= f.lo || n - w >= f.hi);
    let want = 0;
    if (!clear(0)) {
      want = NaN;
      for (let k = 1; k <= NUDGE_MAX * 10 && Number.isNaN(want); k++) {
        const a = -k / 10 * (Math.sign(this.nudgeWant) || -1), b = -a;
        if (clear(a)) want = a; else if (clear(b)) want = b;
      }
      if (Number.isNaN(want)) want = 0;
    }
    this.nudgeWant = want;
    const rate = Math.max(NUDGE_RATE, 0.12 * speed) * dt;
    this.nudge += clamp(want - this.nudge, -rate, rate);
    // Then the speed: what overlaps the corridor (at the shift the car will have by then) is followed or stopped for.
    let target = Infinity;
    this.obstacleGap = null;
    for (const f of found) {
      const reachIn = f.gap / Math.max(speed, 1), shifted = Math.abs(want - this.nudge) <= Math.max(NUDGE_RATE, 0.12 * speed) * reachIn;
      const n = shifted ? want : this.nudge;
      if (n + w <= f.lo || n - w >= f.hi || f.gap < -0.5) continue;
      // Oncoming: the gap closes from both ends. Crossing (a predicted place): stop short of it.
      const follow = Math.max(0, f.along), gap = f.gap + Math.min(0, f.along) * 1.5;
      const free = gap - GAP_STILL;
      const allowed = Math.max(0, Math.min(follow + Math.max(0, free - GAP_TIME * follow) * 0.4,
        Math.sqrt(Math.max(0, follow * follow + 2 * style.brake * 0.9 * Math.max(0, free)))));
      if (allowed < target) { target = allowed; this.obstacleGap = Math.round(f.gap * 10) / 10; }
    }
    return target;
  }

  /** Arc lengths where the path starts a hairpin (HAIRPIN_M, HAIRPIN_COS), sampled every metre. */
  private hairpinsOf(): number[] {
    if (this.hairpins) return this.hairpins;
    const path = this.path, out: number[] = [], a: PathSample = { x: 0, z: 0, y: null, hx: 1, hz: 0 }, b = { ...a };
    for (let s = 0; s + 1 < path.length; s += 1) {
      sampleAt(path, s, a); sampleAt(path, Math.min(path.length, s + HAIRPIN_M), b);
      if (a.hx * b.hx + a.hz * b.hz < HAIRPIN_COS) { out.push(s); s += HAIRPIN_M + 2; }
    }
    return this.hairpins = out;
  }

  /** The next hairpin not yet turned at, at or ahead of `s` (a little behind counts: the car may stop past it). */
  private nextHairpin(s: number) {
    return this.hairpinsOf().find(h => h >= s - 3 && !this.hairpinsDone.has(h)) ?? null;
  }

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
    // Stop before a hairpin: the turn in the street takes it at a standstill.
    const hairpin = this.nextHairpin(s);
    if (hairpin !== null) target = Math.min(target, Math.sqrt(2 * style.brake * 0.8 * Math.max(0, hairpin - s - 0.5)));
    return Math.max(0, target);
  }

  /** The ground check of a pending turn leg: the metres the leg may drive (0: none). A turn's first leg (`pick`) gets
   *  the room of all four first legs instead (`rooms`, TURN_FIRST_LEGS order) and takes the one that gets the car
   *  round soonest: forward with the lock toward the aim, or back with the other lock (both rotate the car the same
   *  way), or either of those the long way round. */
  turnLegChecked(room: number, rooms?: number[]) {
    const t = this.turn, leg = this.pendingTurnLeg;
    if (!t || !leg) return;
    this.pendingTurnLeg = null;
    if (leg.pick && rooms) {
      // Rotation (rad) to the aim turning toward its side, and the long way round.
      const toward = Math.acos(clamp(this.aimCos, -1, 1)), aimSide = Math.sign(this.aimSin) || t.side;
      let best = -1;
      TURN_FIRST_LEGS.forEach(([rotation, dir], i) => {
        const side = rotation * aimSide, need = rotation > 0 ? toward : 2 * Math.PI - toward;
        // A first leg back counts a little less: forward is how a driver starts.
        const score = rooms[i] / Math.max(0.3, need) * (dir > 0 ? 1 : 0.9);
        if (score > best) { best = score; t.side = side; t.dir = dir; room = rooms[i]; }
      });
    }
    t.room = room - 0.3;
  }

  private turnLeg(dir: 1 | -1, pose: VehiclePose, steer?: number, length = TURN_LEG_M) {
    const t = this.turn!;
    t.dir = dir; t.time = 0; t.still = 0; t.from = { x: pose.x, z: pose.z };
    const pick = dir > 0 && steer === undefined && !t.picked;
    if (pick) t.picked = true;
    if (this.options.checkReverse) {
      t.room = 0;
      this.pendingTurnLeg = { steer: steer ?? (dir > 0 ? t.side : -t.side), dir, length, pick, aimSide: Math.sign(this.aimSin) || t.side };
    } else t.room = length;
  }

  /** A turn in the street that got nowhere ends the trip: pursuit at full lock from there is what took cars over
   *  quay edges. */
  private startTurn(side: number, aimS: number | null, pose: VehiclePose, aim: { cos: number; sin: number }, edge = false) {
    this.aimCos = aim.cos; this.aimSin = aim.sin;
    this.turn = { side, dir: 1, rounds: 0, room: 0, from: { x: pose.x, z: pose.z }, time: 0, still: 0, blocked: 0, aimS,
      backup: 'no', edge, legs: 0, picked: false, away: TURN_AWAY_M };
    this.turns++;
    this.turnLeg(1, pose);
  }

  private failTurn() {
    this.turn = null; this.pendingTurnLeg = null;
    this.recoveries++;
    this.gaveUp = 'stuck: no room to turn';
  }

  /** The edge watch's result: metres of the arc with `steer` ahead before an edge. */
  edgeChecked(room: number, steer: number) {
    this.pendingEdgeCheck = null;
    this.edgeAhead = { room, steer, age: 0 };
  }

  /** Starts the reverse-out with `steer` (after the caller's ground check). */
  reverse(steer: number) {
    this.pendingReverse = null; this.stuckFor = 0;
    this.reverseFor = REVERSE_TIME; this.reverseSteer = steer;
    this.reverseFrom = this.lastPose ? { x: this.lastPose.x, z: this.lastPose.z } : null;
  }

  step(pose: VehiclePose, dt: number, obstacles?: readonly Obstacle[]): DriveStep {
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
    // A trip that does not stop arrives on passing within its radius of the end; the next goTo carries on from there.
    if (this.options.stop === false && remain < (this.options.arrive ?? 2.5)) {
      return { command: { throttle: 0, brake: 0, steer: 0, handbrake: false }, status: 'arrived', s: this.s, lateral: at.lateral, target: speed };
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

    // Obstacles (not during a turn in the street): the speed they allow and the shift that passes a still one.
    let obstacleTarget = obstacles?.length && !this.turn ? this.avoidObstacles(pose, speed, dt, obstacles) : Infinity;
    if (!obstacles?.length || this.turn) { this.nudge = 0; this.nudgeWant = 0; this.obstacleGap = null; }
    const wallTarget = this.turn ? Infinity : this.wallSpeed(speed, dt);
    if (wallTarget < obstacleTarget) { obstacleTarget = wallTarget; this.obstacleGap = this.wallGap; }
    // Pure pursuit from the rear axle toward a point `lookahead` metres along the path (shifted `nudge` to its right).
    const rx = pose.x - pose.hx * g.rearOffset, rz = pose.z - pose.hz * g.rearOffset;
    const lookahead = clamp(3.5 + 0.55 * speed, 4, 18);
    const aim = sampleAt(path, Math.min(path.length + 30, this.s + lookahead + g.rearOffset), this.sample);
    // Past the end, aim along the final heading so the car does not swerve onto the end point.
    const over = this.s + lookahead + g.rearOffset - path.length;
    const ax = (over > 0 ? aim.x + aim.hx * over : aim.x) - aim.hz * this.nudge - rx,
      az = (over > 0 ? aim.z + aim.hz * over : aim.z) + aim.hx * this.nudge - rz;
    const ld = Math.hypot(ax, az) || 1;
    // Right of the chassis heading (x east, z south): (-hz, hx).
    const sinAlpha = (ax * -pose.hz + az * pose.hx) / ld;
    const wheelbase = g.rearSteer ? effectiveWheelbase(g.rearSteer, g.wheelbase, g.maxSteer, speed) : g.wheelbase;
    let wheel = Math.atan(2 * wheelbase * sinAlpha / ld);
    // Cross-track damping (Stanley-like): pulls the car onto the lane line at low speed and on long curves.
    wheel += Math.atan(0.35 * -(at.lateral - this.nudge) / (speed + 2)) * 0.5;
    const lock = g.maxSteer / (1 + (speed / g.steerHalfSpeed) ** 2);
    const steer = clamp(wheel / lock, -1, 1);

    // Turn in the street: the aim point behind the car at a standstill.
    const cosAim = (ax * pose.hx + az * pose.hz) / ld;
    // A hairpin turn aims past the hairpin, on the way out of it (pursuit's aim may still lie before it).
    const aimAt = (aimS: number) => {
      const p = sampleAt(path, Math.min(path.length, aimS));
      const dx = p.x - rx, dz = p.z - rz, l = Math.hypot(dx, dz) || 1;
      return { cos: (dx * pose.hx + dz * pose.hz) / l, sin: (dx * -pose.hz + dz * pose.hx) / l };
    };
    if (this.turn) {
      // Done once the car faces the aim and lies along the path there: facing a point of a path that crosses in front
      // (Amsterdam Grimburgwal: 5 m off a hairpin's way out, side-on) left pursuit a full-lock swing over the quay.
      const hairpinTurn = this.turn.aimS !== null;
      const along = hairpinTurn ? sampleAt(path, this.turn.aimS!) : aim;
      if ((hairpinTurn ? aimAt(this.turn.aimS!).cos > HAIRPIN_DONE_COS : cosAim > TURN_DONE_COS)
        && along.hx * pose.hx + along.hz * pose.hz > TURN_DONE_ALONG && (!this.turn.edge || this.turn.legs > 0)) {
        if (this.turn.aimS !== null) this.s = Math.max(this.s, this.turn.aimS - HAIRPIN_M - 4);
        this.turn = null; this.pendingTurnLeg = null;
      }
    }
    // At a hairpin the car has stopped for: turn unless it already faces the way out.
    const hairpin = this.nextHairpin(this.s);
    if (hairpin !== null && hairpin - this.s < 2.5 && speed < 1.5) {
      this.hairpinsDone.add(hairpin);
      // Well along the way out: from where the car stops, a point just past the hairpin can still lie ahead.
      const aimS = hairpin + 2 * HAIRPIN_M + 4, out = aimAt(aimS);
      if (!this.turn && !this.gaveUp && this.turns < TURN_TRIES && out.cos < HAIRPIN_DONE_COS) this.startTurn(Math.sign(out.sin) || 1, aimS, pose, out);
    }
    if (!this.turn && !this.gaveUp && this.turns < TURN_TRIES && speed < 1.5 && cosAim < TURN_START_COS) this.startTurn(Math.sign(sinAlpha) || 1, null, pose, { cos: cosAim, sin: sinAlpha });
    if (this.turn) {
      const t = this.turn, recovering = { s: this.s, lateral: at.lateral, status: 'recovering' as const };
      const backingUp = t.backup === 'driving';
      if (at.distance > OFF_PATH_M - 1) { this.failTurn(); return { command: hold, target: 0, ...recovering }; }
      if (this.pendingTurnLeg) return { command: hold, target: 0, ...recovering };
      if (t.time === 0) t.away = Math.max(TURN_AWAY_M, at.distance + 0.5);
      t.time += dt; t.still = speed < 0.2 ? t.still + dt : 0;
      const moved = Math.hypot(pose.x - t.from.x, pose.z - t.from.z);
      if (moved >= t.room || (!backingUp && at.distance > t.away) || t.time > (backingUp ? 2 * TURN_LEG_S : TURN_LEG_S)
        || (t.time > 1 && t.still > TURN_STALL_S)) {
        if (moved >= 0.3) t.legs++;
        if (backingUp) {
          // Backed up: start the turn again from here, picking its side afresh.
          t.backup = 'done'; t.rounds = 0; t.blocked = 0; t.picked = false;
          const out = t.aimS !== null ? aimAt(t.aimS) : { cos: cosAim, sin: sinAlpha };
          this.aimCos = out.cos; this.aimSin = out.sin; t.side = Math.sign(out.sin) || t.side;
          this.turnLeg(1, pose);
          return { command: hold, target: 0, ...recovering };
        }
        t.blocked = moved < 0.3 ? t.blocked + 1 : 0;
        if (t.dir > 0) t.rounds++;
        if (t.blocked >= 2 || t.rounds >= TURN_ROUNDS) {
          if (t.backup === 'done') { this.failTurn(); return { command: hold, target: 0, ...recovering }; }
          t.backup = 'driving';
          this.turnLeg(-1, pose, 0, TURN_BACKUP_M);
          return { command: hold, target: 0, ...recovering };
        }
        // Rotated past the way out (short legs by a quay: Amsterdam Grimburgwal corner, Windows): rotate back toward
        // it rather than on round the circle.
        const now = t.aimS !== null ? aimAt(t.aimS) : { cos: cosAim, sin: sinAlpha };
        if (Math.abs(now.sin) > TURN_FLIP_SIN && Math.sign(now.sin) !== t.side) t.side = Math.sign(now.sin);
        this.turnLeg(t.dir > 0 ? -1 : 1, pose);
        return { command: hold, target: 0, ...recovering };
      }
      // Walking pace both ways. LT brakes a car rolling forward and reverses once stopped; RT brakes one rolling back.
      const turnSteer = backingUp ? 0 : t.side, pedal = t.still > TURN_PUSH_S ? TURN_PUSH : TURN_PEDAL;
      const command = t.dir > 0
        ? { throttle: v < TURN_SPEED ? pedal : 0, brake: v > TURN_SPEED + 0.5 ? 0.3 : 0, steer: turnSteer, handbrake: false }
        : { throttle: v < -TURN_SPEED - 0.5 ? 0.3 : 0, brake: v > -TURN_SPEED ? pedal : 0, steer: -turnSteer, handbrake: false };
      return { command, target: TURN_SPEED * t.dir, ...recovering };
    }

    const planned = Math.min(this.plannedSpeed(this.s, speed), obstacleTarget);
    let target = cosAim < SIDE_AIM_COS ? Math.min(planned, SIDE_AIM_SPEED) : planned;
    // No faster than the lateral limit of the arc actually steered: the path's own curvature misses a car that sets
    // off across it (Amsterdam Grimburgwal: from rest facing the canal, the car reached 5.8 m/s at full lock and ran
    // wide onto the quay edge).
    const steered = Math.abs(Math.tan(steer * lock)) / wheelbase;
    if (steered > 0.02) target = Math.min(target, Math.max(SIDE_AIM_SPEED, Math.sqrt(STYLE[this.options.style ?? 'normal'].lateral / steered)));
    // Edge watch: a slow tight arc toward an edge stops short of it and turns in the street (guarded legs) instead.
    if (this.options.checkReverse) {
      const style = STYLE[this.options.style ?? 'normal'], e = this.edgeAhead;
      if (e) e.age += dt;
      const watching = speed < EDGE_WATCH_SPEED && Math.abs(steer) > EDGE_WATCH_STEER;
      if (watching && !this.pendingEdgeCheck && (!e || e.age > EDGE_WATCH_S))
        this.pendingEdgeCheck = { steer, length: 2 + EDGE_WATCH_SPEED ** 2 / (2 * style.brake) };
      if (watching && e && e.age < 0.6 && Math.abs(e.steer - steer) < 0.4 && e.room < speed * speed / (2 * style.brake) + 0.5) {
        target = 0;
        if (speed < 0.4) {
          this.edgeAhead = null;
          if (++this.edgeStops >= EDGE_STOPS || this.turns >= TURN_TRIES) { this.gaveUp = 'stuck: edge ahead'; return { command: hold, status: 'recovering', s: this.s, lateral: at.lateral, target: 0 }; }
          this.startTurn(Math.sign(sinAlpha) || 1, null, pose, { cos: cosAim, sin: sinAlpha }, true);
          return { command: hold, status: 'recovering', s: this.s, lateral: at.lateral, target: 0 };
        }
      }
    }
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
    if (this.stuckFor > STUCK_AFTER || this.wallBlocked) {
      this.recoveries++;
      this.stuckFor = 0; this.wallBlocked = false;
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
