import type { CharacterState, Quat, RemoteCarDraw, VehicleState } from '../physics/protocol';
import type { V3 } from '../core/origin';
import { PoseCorrection, SourceClock, quatConjugate, quatExp, quatLog, quatMultiply } from './renderClock';
const STEP = 1 / 60;
/** Render extrapolation past the newest physics state (s): a late worker snapshot keeps the player's car and
 *  character moving along their last velocity and turn rate instead of freezing, at full speed for EXTRAPOLATION_FULL,
 *  then slowing smoothly to a stop at most MAX_EXTRAPOLATION ahead. Render only: the physics state (and so
 *  multiplayer) never sees it. */
const EXTRAPOLATION_FULL = 0.15, EXTRAPOLATION_FADE = 0.1;
export const MAX_EXTRAPOLATION = EXTRAPOLATION_FULL + EXTRAPOLATION_FADE;
/** Seconds of motion carried on `late` seconds past the newest state (continuous speed, bounded). */
export function extrapolated(late: number) {
  return late <= EXTRAPOLATION_FULL ? late : EXTRAPOLATION_FULL + EXTRAPOLATION_FADE * (1 - Math.exp(-(late - EXTRAPOLATION_FULL) / EXTRAPOLATION_FADE));
}
/** Render delay bounds (s) on top of the fastest recent delivery: SourceClock adds the delivery jitter. */
export const RENDER_MIN_DELAY = 0.002;
const MIN_DELAY = RENDER_MIN_DELAY, MAX_DELAY = 0.1;
/** Consecutive states farther apart than this are a teleport: no interpolation or smoothing across it. */
const TELEPORT = 2;
const KEEP = 0.25;

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const angle = (a: number, b: number, t: number) => a + Math.atan2(Math.sin(b - a), Math.cos(b - a)) * t;
const vector = (a: V3, b: V3, t: number) => ({ x: lerp(a.x, b.x, t), y: lerp(a.y, b.y, t), z: lerp(a.z, b.z, t) });
const ahead = (p: V3, v: V3, s: number) => ({ x: p.x + v.x * s, y: p.y + v.y * s, z: p.z + v.z * s });
const distance = (a: V3, b: V3) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
function rotation(a: Quat, b: Quat, t: number): Quat {
  const sign = a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w < 0 ? -1 : 1;
  const q = { x: lerp(a.x, b.x * sign, t), y: lerp(a.y, b.y * sign, t),
    z: lerp(a.z, b.z * sign, t), w: lerp(a.w, b.w * sign, t) };
  const length = Math.hypot(q.x, q.y, q.z, q.w) || 1;
  return { x: q.x / length, y: q.y / length, z: q.z / length, w: q.w / length };
}

export interface PoseOps<T> {
  lerp(a: T, b: T, f: number): T;
  /** `b` carried on for `s` seconds at the rates between `a` (`dt` earlier) and `b`. */
  extrapolate(a: T, b: T, dt: number, s: number): T;
  pos(s: T): V3;
  rot(s: T): Quat | undefined;
  speed(s: T): number;
  with(s: T, pos: V3, rot: Quat | undefined): T;
}
interface Entry<T> { t: number; s: T }

/** Timestamped states drawn at one SourceClock: interpolated between the two states around the drawn time,
 *  extrapolated past the newest for up to MAX_EXTRAPOLATION, and a late state's jump against what was drawn is
 *  blended out by a critically damped PoseCorrection. Local physics actors and network remotes share it. */
export class PoseTrack<T> {
  private entries: Entry<T>[] = [];
  readonly clock: SourceClock;
  private correction = new PoseCorrection();
  /** The newest two entries when the last drawn time was past the newest one: what that frame extrapolated from. */
  private beyond: Entry<T>[] | null = null;
  /** The last drawn source time and how far past the newest state it was (s), for diagnostics and tests. */
  drawn = { t: 0, late: 0 };
  private readonly carry: (late: number) => number;
  /** `minDelay`/`maxDelay`: render delay bounds (s) around the delivery jitter; `intervals`: how many median state
   *  spacings to add to the delay (sparse network poses); `carry`: seconds of motion extrapolated `late` s past the
   *  newest state. */
  constructor(private readonly ops: PoseOps<T>, options: { minDelay?: number; maxDelay?: number; intervals?: number; carry?: (late: number) => number } = {}) {
    this.clock = new SourceClock(options.minDelay ?? MIN_DELAY, options.maxDelay ?? MAX_DELAY, options.intervals ?? 0);
    this.carry = options.carry ?? extrapolated;
  }
  get newest() { return this.entries.at(-1)?.s ?? null; }
  clear() { this.entries = []; this.beyond = null; this.correction.reset(); }
  /** Adds the state of source time `t` (s), available since local time `arrival` (s). `observe`: false for an
   *  older state delivered alongside (it says nothing about delivery time). A teleport clears the track. */
  push(t: number, state: T, arrival: number, observe = true, continuous = (a: T, b: T) => distance(this.ops.pos(a), this.ops.pos(b)) < TELEPORT) {
    const last = this.entries.at(-1);
    if (last && t === last.t) last.s = state;
    else if (last && t < last.t && t > last.t - 1) return; // an older state than one already held
    else {
      // A new epoch (a restarted worker, a rejoin) or a teleport: nothing to interpolate from.
      if (last && t < last.t) { this.clear(); this.clock.reset(); }
      else if (last && !continuous(last.s, state)) this.clear();
      this.entries.push({ t, s: state });
    }
    if (observe) this.clock.observe(t, arrival);
  }
  private evaluate(entries: Entry<T>[], t: number): { s: T; late: number } {
    const first = entries[0], last = entries.at(-1)!;
    if (t <= first.t) return { s: first.s, late: 0 };
    for (let i = 1; i < entries.length; i++) {
      const a = entries[i - 1], b = entries[i];
      if (t <= b.t) return { s: this.ops.lerp(a.s, b.s, (t - a.t) / (b.t - a.t)), late: 0 };
    }
    const late = t - last.t, prev = entries.at(-2);
    if (!prev) return { s: last.s, late };
    return { s: this.ops.extrapolate(prev.s, last.s, last.t - prev.t, this.carry(late)), late };
  }
  private velocity(entries: Entry<T>[], t: number): V3 {
    const a = this.ops.pos(this.evaluate(entries, t - 0.004).s), b = this.ops.pos(this.evaluate(entries, t).s);
    return { x: (b.x - a.x) / 0.004, y: (b.y - a.y) / 0.004, z: (b.z - a.z) / 0.004 };
  }
  /** The pose to draw at local time `now` (s). `extrapolate`: false while the simulation is paused (the newest
   *  state is then simply held). */
  sample(now: number, extrapolate = true): T | null {
    if (!this.entries.length) return null;
    let t = this.clock.at(now)!;
    const newest = this.entries.at(-1)!;
    if (!extrapolate) t = Math.min(t, newest.t);
    const raw = this.evaluate(this.entries, t);
    this.correction.advance(now);
    // A state arrived after a frame drawn past the then-newest one: the pose that frame was heading for and the new
    // one differ at the same time. The difference (position, velocity, rotation) is added to a critically damped
    // correction, so the drawn pose and its velocity carry on through it instead of snapping.
    if (this.beyond && this.beyond.at(-1)!.t < newest.t) {
      const old = extrapolate ? this.evaluate(this.beyond, t).s : this.beyond.at(-1)!.s;
      const p = this.ops.pos(raw.s), q = this.ops.pos(old), a = this.ops.rot(raw.s), b = this.ops.rot(old);
      const pv = this.velocity(this.entries, t), qv = extrapolate ? this.velocity(this.beyond, t) : { x: 0, y: 0, z: 0 };
      this.correction.add({ x: q.x - p.x, y: q.y - p.y, z: q.z - p.z }, { x: qv.x - pv.x, y: qv.y - pv.y, z: qv.z - pv.z },
        a && b ? quatLog(quatMultiply(b, quatConjugate(a))) : null, this.ops.speed(raw.s));
    }
    this.beyond = raw.late > 0 ? this.entries.slice(-2) : null;
    while (this.entries.length > 2 && this.entries[1].t < t - KEEP) this.entries.shift();
    this.drawn = { t, late: raw.late };
    const c = this.correction.value();
    const p = this.ops.pos(raw.s), r = this.ops.rot(raw.s);
    const out = c ? this.ops.with(raw.s, ahead(p, c.dp, 1), r ? quatMultiply(c.dr, r) : undefined) : raw.s;
    return out;
  }
}

const vehicleOps: PoseOps<VehicleState> = {
  lerp: (p, c, a) => ({ ...c, pos: vector(p.pos, c.pos, a), rot: rotation(p.rot, c.rot, a), linvel: vector(p.linvel, c.linvel, a),
    wheels: c.wheels.map((w, i) => p.wheels[i] ? { ...w, pos: vector(p.wheels[i].pos, w.pos, a),
      steer: angle(p.wheels[i].steer, w.steer, a), rotation: angle(p.wheels[i].rotation, w.rotation, a) } : w) }),
  extrapolate: (p, c, dt, s) => {
    // The chassis carries on along its velocity and last turn rate, the wheels at their spin (chassis-local).
    const turn = quatLog(quatMultiply(c.rot, quatConjugate(p.rot))), k = dt > 0 ? s / dt : 0;
    return { ...c, pos: ahead(c.pos, c.linvel, s), rot: quatMultiply(quatExp({ x: turn.x * k, y: turn.y * k, z: turn.z * k }), c.rot),
      wheels: c.wheels.map((w, i) => p.wheels[i] ? { ...w, rotation: w.rotation + Math.atan2(Math.sin(w.rotation - p.wheels[i].rotation), Math.cos(w.rotation - p.wheels[i].rotation)) * k } : w) };
  },
  pos: s => s.pos, rot: s => s.rot, speed: s => s.speed,
  with: (s, pos, rot) => ({ ...s, pos, rot: rot ?? s.rot }),
};
const characterOps: PoseOps<CharacterState> = {
  lerp: (p, c, a) => ({ ...c, pos: vector(p.pos, c.pos, a) }),
  extrapolate: (_p, c, _dt, s) => ({ ...c, pos: ahead(c.pos, c.vel, s) }),
  pos: s => s.pos, rot: () => undefined, speed: s => Math.hypot(s.vel.x, s.vel.z),
  with: (s, pos) => ({ ...s, pos }),
};

/** The render pose of the local character. `push` takes each worker snapshot's newest state and the one a fixed
 *  step before it, its tick and the local time (s) it was published. */
export class CharacterInterpolation {
  readonly track = new PoseTrack(characterOps);
  private tick = -1;
  push(current: CharacterState | null, previous: CharacterState | null, tick: number, arrival: number) {
    if (tick === this.tick) return;
    this.tick = tick;
    if (!current) { this.track.clear(); return; }
    const continuous = (a: CharacterState, b: CharacterState) => a.enabled === b.enabled && distance(a.pos, b.pos) < TELEPORT;
    if (previous) this.track.push((tick - 1) * STEP, previous, arrival, false, continuous);
    this.track.push(tick * STEP, current, arrival, true, continuous);
  }
  sample(now: number, extrapolate = true): CharacterState | null { return this.track.sample(now, extrapolate); }
}

/** The render pose of the driven vehicle (the start car while on foot), wheels included. */
export class VehicleInterpolation {
  readonly track = new PoseTrack(vehicleOps);
  private tick = -1;
  push(current: VehicleState | null, previous: VehicleState | null, tick: number, arrival: number) {
    if (tick === this.tick) return;
    this.tick = tick;
    if (!current) { this.track.clear(); return; }
    const continuous = (a: VehicleState, b: VehicleState) => a.id === b.id && a.wheels.length === b.wheels.length && distance(a.pos, b.pos) < TELEPORT;
    if (previous) this.track.push((tick - 1) * STEP, previous, arrival, false, continuous);
    this.track.push(tick * STEP, current, arrival, true, continuous);
  }
  sample(now: number, extrapolate = true): VehicleState | null { return this.track.sample(now, extrapolate); }
}

const remoteCarOps: PoseOps<RemoteCarDraw> = {
  lerp: (p, c, a) => ({ ...c, pos: vector(p.pos, c.pos, a), rot: rotation(p.rot, c.rot, a), vel: vector(p.vel, c.vel, a) }),
  extrapolate: (p, c, dt, s) => {
    const turn = quatLog(quatMultiply(c.rot, quatConjugate(p.rot))), k = dt > 0 ? s / dt : 0;
    return { ...c, pos: ahead(c.pos, c.vel, s), rot: quatMultiply(quatExp({ x: turn.x * k, y: turn.y * k, z: turn.z * k }), c.rot) };
  },
  pos: s => s.pos, rot: s => s.rot, speed: s => Math.hypot(s.vel.x, s.vel.z),
  with: (s, pos, rot) => ({ ...s, pos, rot: rot ?? s.rot }),
};

/** The render poses of the other players' cars simulated here (physics/world.ts REMOTE_SIM), on the same clock as the
 *  driven vehicle: the worker steps at 60 Hz, and drawn at its raw steps a moving car would advance every other frame
 *  at 120 Hz (and two steps at once now and then). A placement (`snaps`) starts its track afresh. */
export class RemoteCarInterpolation {
  private readonly tracks = new Map<string, PoseTrack<RemoteCarDraw>>();
  private tick = -1;
  push(cars: RemoteCarDraw[], tick: number, arrival: number) {
    if (tick === this.tick) return;
    this.tick = tick;
    const continuous = (a: RemoteCarDraw, b: RemoteCarDraw) => a.snaps === b.snaps && distance(a.pos, b.pos) < TELEPORT;
    const seen = new Set<string>();
    for (const car of cars) {
      seen.add(car.id);
      let track = this.tracks.get(car.id);
      if (!track) this.tracks.set(car.id, track = new PoseTrack(remoteCarOps));
      track.push(tick * STEP, car, arrival, true, continuous);
    }
    for (const id of [...this.tracks.keys()]) if (!seen.has(id)) this.tracks.delete(id);
  }
  sample(now: number, extrapolate = true): RemoteCarDraw[] {
    const out: RemoteCarDraw[] = [];
    for (const track of this.tracks.values()) { const s = track.sample(now, extrapolate); if (s) out.push(s); }
    return out;
  }
}
