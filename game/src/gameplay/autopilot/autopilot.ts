// The autopilot: drives the local player's character, or the vehicle the character is in, from A to B.
//
//   const trip = autopilot.goTo({ x, z }, { mode: 'drive', speed: 14, style: 'normal' });
//   trip.progress; await trip.done; trip.cancel();
//
// It produces the inputs a controller would (input/index.ts `driver`): throttle, brake, steer and handbrake in a
// vehicle; the move stick, sprint, walk and jump on foot. The real vehicle physics, collisions and multiplayer
// pose sync therefore all run as for a player; nothing here writes physics state or teleports.
//
// Routing: lanes of the ambient-traffic graph for driving (lanes.ts), the street router's foot network for
// walking; an explicit lane or point path can be followed exactly (drive tests). Multiplayer: every client drives
// only its own player, and the lane graph is a pure function of the tiles' road data, so missions behave alike on
// every client; nothing is shared that other clients must agree on beyond the normal player pose.
import { VEHICLES, type VehicleKind } from '../../physics/constants';
import type { VehicleState } from '../../physics/protocol';
import type { Actions } from '../../input/bindings';
import { DriveFollower, WalkFollower, type DriveStyle, type VehicleGeometry, type VehiclePose } from './follow';
import { tilesBetween, type LaneRef, type LaneRouter, type LaneRoute } from './lanes';
export { tilesBetween } from './lanes';
import { makePath, type Path, type PathPoint } from './path';

export type AutopilotMode = 'drive' | 'walk';
export type AutopilotState = 'planning' | 'driving' | 'arrived' | 'failed' | 'cancelled';

export interface GoToOptions {
  /** Default: drive when in a vehicle, else walk. */
  mode?: AutopilotMode;
  /** Follow exactly this lane (extended to enter and leave it at speed) or this point path, instead of routing. */
  lane?: LaneRef;
  path?: readonly PathPoint[];
  /** Cruise speed (m/s). Driving defaults to each lane's posted limit; walking to running pace. */
  speed?: number;
  style?: DriveStyle;
  /** Walking pace. */
  pace?: 'walk' | 'run' | 'sprint';
  /** Stop at the end (default) or keep the last command (a chained goTo continues). */
  stop?: boolean;
  /** Arrival radius (m). */
  arrive?: number;
  /** Give up after this many seconds (default: 4 s per 10 m of route plus 60 s). */
  timeout?: number;
  /** Stuck recoveries (reverse, re-plan) before giving up (default 4). */
  recoveries?: number;
  /** A human stick or pedal input cancels the trip (default true; a cutscene drive sets false). */
  takeover?: boolean;
}

export interface AutopilotProgress {
  /** Metres along the path, its length, and the share done. */
  s: number; length: number; fraction: number;
  speed: number; target: number;
  /** Metres right of the path. */
  lateral: number;
  recoveries: number; replans: number;
  elapsed: number;
}

export interface AutopilotResult { state: 'arrived' | 'failed' | 'cancelled'; reason?: string; progress: AutopilotProgress }

export interface AutopilotHandle {
  readonly id: number;
  readonly mode: AutopilotMode;
  readonly state: AutopilotState;
  readonly reason: string | undefined;
  readonly progress: AutopilotProgress;
  readonly path: Path | null;
  readonly lanes: readonly LaneRef[];
  readonly done: Promise<AutopilotResult>;
  cancel(reason?: string): void;
}

/** What the autopilot reads of the game: the player's mode and pose, the camera basis (the walking stick is
 *  camera-relative), and the routers. */
export interface AutopilotWorld {
  mode(): 'foot' | 'car';
  /** The driven vehicle's latest state (physics snapshot). */
  vehicle(): VehicleState | null;
  feet(): { x: number; y: number; z: number };
  cameraBasis(): { fx: number; fz: number; rx: number; rz: number };
  lanes: LaneRouter;
  /** Walking route over the street/foot network (gameplay/router.ts), or null. */
  footRoute(from: PathPoint, to: PathPoint): PathPoint[] | null;
  /** Driving fallback over street centrelines when no lane route exists. */
  carRoute?(from: PathPoint, to: PathPoint): PathPoint[] | null;
}

export function vehicleGeometry(kind: VehicleKind = 'car'): VehicleGeometry {
  const t = VEHICLES[kind];
  const steered = t.steered.map(i => t.wheels[i].x), rest = t.wheels.filter((_, i) => !t.steered.includes(i)).map(w => w.x);
  const front = steered.reduce((a, b) => a + b, 0) / steered.length, rear = Math.min(...rest);
  return { wheelbase: front - rear, rearOffset: -rear, maxSteer: t.maxSteer, steerHalfSpeed: t.steerHalfSpeed };
}

/** Chassis pose from a vehicle snapshot: forward is chassis +X rotated by the body quaternion. */
export function vehiclePose(v: VehicleState): VehiclePose {
  const { x, y, z, w } = v.rot;
  // Rotate (1, 0, 0) by q: the first column of the rotation matrix.
  const fx = 1 - 2 * (y * y + z * z), fz = 2 * (x * z - w * y);
  const d = Math.hypot(fx, fz) || 1;
  return { x: v.pos.x, y: v.pos.y, z: v.pos.z, hx: fx / d, hz: fz / d, forwardSpeed: v.forwardSpeed };
}

const HUMAN = 0.35;
const DRIVE_AXES = ['throttle', 'brake', 'steer'] as const;
const WALK_AXES = ['moveX', 'moveY'] as const;

class Trip implements AutopilotHandle {
  state: AutopilotState = 'planning';
  reason: string | undefined;
  path: Path | null = null;
  lanes: LaneRef[] = [];
  drive: DriveFollower | null = null;
  walk: WalkFollower | null = null;
  limit: ((s: number) => number) | null = null;
  elapsed = 0; replans = 0; speed = 0; target = 0; lateral = 0;
  planning: Promise<void> | null = null;
  readonly done: Promise<AutopilotResult>;
  private resolve!: (r: AutopilotResult) => void;
  constructor(readonly id: number, readonly mode: AutopilotMode, readonly goal: PathPoint, readonly options: GoToOptions,
    private readonly owner: Autopilot) {
    this.done = new Promise(r => { this.resolve = r; });
  }
  get progress(): AutopilotProgress {
    const f = this.drive ?? this.walk, length = this.path?.length ?? 0, s = f?.s ?? 0;
    return { s, length, fraction: length ? Math.min(1, s / length) : 0, speed: this.speed, target: this.target, lateral: this.lateral,
      recoveries: f?.recoveries ?? 0, replans: this.replans, elapsed: this.elapsed };
  }
  finish(state: 'arrived' | 'failed' | 'cancelled', reason?: string) {
    if (this.state === 'arrived' || this.state === 'failed' || this.state === 'cancelled') return;
    this.state = state; this.reason = reason;
    this.owner.release(this);
    this.resolve({ state, reason, progress: this.progress });
  }
  cancel(reason = 'cancelled') { this.finish('cancelled', reason); }
}

export class Autopilot {
  private trip: Trip | null = null;
  private next = 1;
  /** The last command sent, for HUDs and traces. */
  command: { throttle: number; brake: number; steer: number; handbrake: boolean } | null = null;
  /** The running trip's route every ~50 m, for the streaming route hint (CoarseTiles.setRouteHint; play.ts). */
  hint: { x: number; z: number }[] | null = null;

  constructor(private readonly world: AutopilotWorld) {}

  get active(): AutopilotHandle | null { return this.trip; }
  /** The lane router (drive tests and the dev console plan with it directly). */
  get lanes(): LaneRouter { return this.world.lanes; }

  /** Starts a trip to `target`, cancelling any running one. */
  goTo(target: PathPoint, options: GoToOptions = {}): AutopilotHandle {
    this.trip?.cancel('replaced');
    const mode = options.mode ?? (this.world.mode() === 'car' ? 'drive' : 'walk');
    const trip = new Trip(this.next++, mode, target, options, this);
    this.trip = trip;
    if ((mode === 'drive') !== (this.world.mode() === 'car')) {
      trip.finish('failed', mode === 'drive' ? 'not in a vehicle' : 'in a vehicle');
      return trip;
    }
    trip.planning = this.plan(trip).catch(error => trip.finish('failed', `planning: ${(error as Error).message}`))
      .finally(() => { trip.planning = null; });
    return trip;
  }

  /** Follows one lane exactly (drive tests): goTo with the lane's own path as the route. */
  followLane(lane: LaneRef, options: Omit<GoToOptions, 'lane' | 'mode'> = {}): AutopilotHandle {
    return this.goTo({ x: 0, z: 0 }, { ...options, mode: 'drive', lane });
  }

  cancel(reason?: string) { this.trip?.cancel(reason); }

  /** Called by the trip when it ends. */
  release(trip: Trip) {
    if (this.trip !== trip) return;
    this.trip = null; this.command = null; this.hint = null;
  }

  private async plan(trip: Trip, replan = false) {
    const { options } = trip;
    if (options.path) return this.install(trip, makePath(options.path as PathPoint[]), null, []);
    if (trip.mode === 'walk') {
      const feet = this.world.feet();
      const points = this.world.footRoute(feet, trip.goal) ?? [feet, trip.goal];
      return this.install(trip, makePath([feet, ...points.slice(1)]), null, []);
    }
    const v = this.world.vehicle();
    if (!v) throw new Error('no vehicle state');
    const pose = vehiclePose(v);
    let route: LaneRoute | null = null;
    if (options.lane && !replan) {
      await this.world.lanes.ensure(tilesBetween(pose, pose, 1));
      route = this.world.lanes.follow(options.lane, 70, 40);
      if (!route) throw new Error(`unknown lane ${options.lane.tile}:${options.lane.id}`);
    } else {
      const goal: PathPoint = options.lane ? (() => { const p = trip.path!; return { x: p.points[p.points.length - 2], z: p.points[p.points.length - 1] }; })() : trip.goal;
      await this.world.lanes.ensure(tilesBetween(pose, goal, 1));
      if (trip.state !== 'planning' && trip.state !== 'driving') return;
      route = this.world.lanes.route({ x: pose.x, z: pose.z, y: pose.y, hx: pose.hx, hz: pose.hz }, { x: goal.x, z: goal.z, y: goal.y });
      if (!route) {
        const street = this.world.carRoute?.({ x: pose.x, z: pose.z }, goal);
        if (!street) throw new Error('no lane route');
        route = { path: makePath(street), lanes: [], limit: () => 13.9 };
      }
      // Join the route from where the car is, not from the lane's snapped point.
      route = { ...route, path: makePath([{ x: pose.x, z: pose.z, y: pose.y }, ...pathAfter(route.path, pose)]) };
    }
    this.install(trip, route.path, route.limit, route.lanes);
  }

  private install(trip: Trip, path: Path, limit: ((s: number) => number) | null, lanes: LaneRef[]) {
    if (trip.state !== 'planning' && trip.state !== 'driving') return;
    trip.path = path; trip.lanes = lanes; trip.limit = limit;
    const o = trip.options;
    if (trip.mode === 'drive') {
      const kind = this.world.vehicle()?.kind ?? 'car';
      const speed = o.speed ?? 30;
      const cap = limit ? (s: number) => Math.min(limit(s), speed) : undefined;
      if (trip.drive) { trip.drive.setPath(path); trip.drive.options = { ...trip.drive.options, limit: cap }; }
      else trip.drive = new DriveFollower(path, vehicleGeometry(kind), { speed: limit ? Math.min(speed, 40) : (o.speed ?? 12), style: o.style, stop: o.stop, arrive: o.arrive, limit: cap });
    } else if (trip.walk) trip.walk.setPath(path);
    else trip.walk = new WalkFollower(path, { pace: o.pace ?? 'run', arrive: o.arrive });
    trip.state = 'driving';
    this.hint = sparse(path);
  }

  /** Input driver (input/index.ts): reads the merged human actions and writes the autopilot's. */
  control(actions: Actions, dt: number) {
    const trip = this.trip;
    if (!trip) return;
    const o = trip.options;
    trip.elapsed += dt;
    const human = (trip.mode === 'drive' ? DRIVE_AXES : WALK_AXES).find(a => Math.abs(actions[a]) > HUMAN);
    if (human && o.takeover !== false && trip.elapsed > 0.3) { trip.cancel(`takeover (${human} ${actions[human].toFixed(2)})`); return; }
    const timeout = o.timeout ?? (trip.path ? 60 + trip.path.length * 0.4 : 120);
    if (trip.elapsed > timeout) { trip.finish('failed', `timeout after ${Math.round(trip.elapsed)} s`); return; }
    if (trip.mode === 'drive') this.drive(trip, actions, dt);
    else this.walkStep(trip, actions, dt);
  }

  private drive(trip: Trip, actions: Actions, dt: number) {
    const v = this.world.vehicle();
    const hold = () => { actions.throttle = 0; actions.brake = 0; actions.steer = 0; actions.handbrake = 1; };
    if (this.world.mode() !== 'car' || !v) { trip.finish('failed', 'left the vehicle'); return; }
    if (trip.state === 'planning' || !trip.drive) { hold(); return; }
    const pose = vehiclePose(v);
    const step = trip.drive.step(pose, dt);
    trip.speed = Math.abs(pose.forwardSpeed); trip.target = step.target; trip.lateral = step.lateral;
    const c = step.command;
    actions.throttle = c.throttle; actions.brake = c.brake; actions.steer = c.steer; actions.handbrake = c.handbrake ? 1 : 0;
    this.command = c;
    if (step.status === 'arrived') { trip.finish('arrived'); return; }
    if (trip.drive.recoveries > (trip.options.recoveries ?? 4)) { trip.finish('failed', 'stuck'); return; }
    const replan = step.status === 'off-path' || (step.status === 'stuck' && trip.drive.recoveries >= 2);
    if (replan && !trip.planning && !trip.options.path) {
      trip.replans++;
      if (trip.replans > 6) { trip.finish('failed', step.status); return; }
      if (step.status === 'off-path') hold();
      trip.planning = this.plan(trip, true).catch(error => trip.finish('failed', `re-plan: ${(error as Error).message}`))
        .finally(() => { trip.planning = null; });
    } else if (step.status === 'off-path' && !trip.planning) trip.finish('failed', 'off path');
  }

  private walkStep(trip: Trip, actions: Actions, dt: number) {
    if (this.world.mode() !== 'foot') { trip.finish('failed', 'entered a vehicle'); return; }
    if (trip.state === 'planning' || !trip.walk) { actions.moveX = 0; actions.moveY = 0; return; }
    const feet = this.world.feet();
    const step = trip.walk.step(feet, dt);
    trip.lateral = step.lateral;
    const b = this.world.cameraBasis();
    // play.ts maps the stick through the camera basis (world = f * moveY + r * moveX); invert it.
    actions.moveY = step.dx * b.fx + step.dz * b.fz;
    actions.moveX = step.dx * b.rx + step.dz * b.rz;
    actions.sprint = step.sprint ? 1 : 0; actions.walk = step.walk ? 1 : 0; actions.jump = step.jump ? 1 : 0;
    if (step.status === 'arrived') { actions.moveX = actions.moveY = 0; trip.finish('arrived'); return; }
    if (step.status === 'stuck' || step.status === 'off-path') {
      if (trip.walk.recoveries > (trip.options.recoveries ?? 4) || trip.options.path) { trip.finish('failed', step.status); return; }
      trip.replans++;
      if (!trip.planning) trip.planning = this.plan(trip, true).catch(e => trip.finish('failed', (e as Error).message)).finally(() => { trip.planning = null; });
    }
  }
}

/** The route from the car onward: drop the part behind it, so a re-plan never steers back to the snapped start. */
function pathAfter(path: Path, pose: { x: number; z: number; hx: number; hz: number }): PathPoint[] {
  const out: PathPoint[] = [];
  for (let i = 0; i < path.cum.length; i++) {
    const x = path.points[2 * i], z = path.points[2 * i + 1];
    const ahead = (x - pose.x) * pose.hx + (z - pose.z) * pose.hz;
    if (!out.length && ahead < 4 && i < path.cum.length - 1) continue;
    out.push({ x, z, ...(path.ys ? { y: path.ys[i] } : {}) });
  }
  return out.length ? out : [{ x: path.points[path.points.length - 2], z: path.points[path.points.length - 1] }];
}

function sparse(path: Path): { x: number; z: number }[] {
  const out: { x: number; z: number }[] = [];
  let last = -Infinity;
  for (let i = 0; i < path.cum.length; i++) if (path.cum[i] - last >= 50 || i === path.cum.length - 1) {
    out.push({ x: path.points[2 * i], z: path.points[2 * i + 1] }); last = path.cum[i];
  }
  return out;
}
