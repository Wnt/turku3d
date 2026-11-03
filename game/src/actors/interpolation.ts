import type { CharacterState, Quat, VehicleState } from '../physics/protocol';
import type { V3 } from '../core/origin';
const STEP = 1 / 60;
/** Longest render extrapolation past the newest physics state (s): a late worker snapshot keeps the player's car
 *  and character moving along their last velocity instead of freezing; beyond this they hold. Render only: the
 *  physics state (and so multiplayer) never sees it. */
export const MAX_EXTRAPOLATION = 0.15;
/** Teleports (resets, seat changes) are never smoothed. */
const MAX_CORRECTION = 3;
/** Fixed steps since the state was received: 0..1 interpolates the latest pair, above 1 extrapolates. */
const phase = (now: number, received: number, remainder: number) => Math.max(0, (now - received + remainder) / STEP);
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const angle = (a: number, b: number, t: number) => a + Math.atan2(Math.sin(b - a), Math.cos(b - a)) * t;
const vector = (a: V3, b: V3, t: number) => ({ x: lerp(a.x, b.x, t), y: lerp(a.y, b.y, t), z: lerp(a.z, b.z, t) });
const ahead = (p: V3, v: V3, s: number) => ({ x: p.x + v.x * s, y: p.y + v.y * s, z: p.z + v.z * s });
function rotation(a: Quat, b: Quat, t: number): Quat {
  const sign = a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w < 0 ? -1 : 1;
  const q = { x: lerp(a.x, b.x * sign, t), y: lerp(a.y, b.y * sign, t),
    z: lerp(a.z, b.z * sign, t), w: lerp(a.w, b.w * sign, t) };
  const length = Math.hypot(q.x, q.y, q.z, q.w) || 1;
  return { x: q.x / length, y: q.y / length, z: q.z / length, w: q.w / length };
}
const multiply = (a: Quat, b: Quat): Quat => ({
  x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y, y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
  z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w, w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z });
const conjugate = (q: Quat): Quat => ({ x: -q.x, y: -q.y, z: -q.z, w: q.w });
const IDENTITY: Quat = { x: 0, y: 0, z: 0, w: 1 };
/** `q` raised to the power `t` (t >= 0): the same turn, scaled. */
function scaleTurn(q: Quat, t: number): Quat {
  const s = q.w < 0 ? -1 : 1, w = Math.min(1, q.w * s), half = Math.acos(w), sin = Math.sin(half);
  if (sin < 1e-9) return IDENTITY;
  const k = Math.sin(half * t) / sin * s;
  return { x: q.x * k, y: q.y * k, z: q.z * k, w: Math.cos(half * t) };
}

/** When a state arrives after the render pose ran ahead of the previous one, the difference fades out instead of
 *  snapping: at most half the actor's speed is spent closing it, so a correction never moves the car backwards. */
class Correction {
  private pos: V3 | null = null;
  private rot: Quat | null = null;
  private at = 0;
  private tau = 0.1;
  private pending = false;
  /** The latest drawn pose, when, at what velocity, and whether it was extrapolated. */
  shown: { pos: V3; rot?: Quat; vel: V3; at: number; extrapolated: boolean } | null = null;
  arrived() { if (this.shown?.extrapolated) this.pending = true; }
  reset() { this.pos = this.rot = null; this.pending = false; }
  apply(now: number, pos: V3, rot: Quat | undefined, speed: number): { pos: V3; rot?: Quat } {
    if (this.pending && this.shown) {
      this.pending = false;
      // Where the drawn pose would be now had it kept going, against the new state's pose.
      const was = ahead(this.shown.pos, this.shown.vel, Math.max(0, now - this.shown.at));
      const d = { x: was.x - pos.x, y: was.y - pos.y, z: was.z - pos.z };
      const size = Math.hypot(d.x, d.y, d.z);
      if (size < MAX_CORRECTION) {
        this.pos = d; this.at = now;
        this.tau = Math.min(0.4, Math.max(0.08, size / (0.5 * Math.max(speed, 1))));
        this.rot = rot && this.shown.rot ? multiply(this.shown.rot, conjugate(rot)) : null;
      } else this.reset();
    }
    if (!this.pos) return { pos, rot };
    const k = Math.exp(-(now - this.at) / this.tau);
    if (k < 1e-3) { this.reset(); return { pos, rot }; }
    return { pos: ahead(pos, this.pos, k), rot: rot && this.rot ? multiply(scaleTurn(this.rot, k), rot) : rot };
  }
}

/** One fixed-step of render latency; packets with no new physics tick never reset the clock. A late snapshot
 *  extrapolates along the last velocity for up to MAX_EXTRAPOLATION. */
export class CharacterInterpolation {
  private current: CharacterState | null = null;
  private previous: CharacterState | null = null;
  private tick = -1;
  private received = 0;
  private remainder = 0;
  private correction = new Correction();
  push(current: CharacterState | null, previous: CharacterState | null, tick: number, remainder: number, now: number) {
    if (tick === this.tick) return;
    this.tick = tick; this.received = now; this.remainder = remainder;
    this.current = current;
    this.previous = previous;
    if (current && previous && (current.enabled !== previous.enabled ||
      Math.hypot(current.pos.x - previous.pos.x, current.pos.y - previous.pos.y, current.pos.z - previous.pos.z) > 2)) {
      this.previous = current; this.correction.reset(); this.correction.shown = null;
    }
    this.correction.arrived();
  }
  /** `extrapolate`: false while the simulation is paused (a late state is then simply the latest). */
  sample(now: number, extrapolate = true): CharacterState | null {
    const c = this.current, p = this.previous;
    if (!c || !p) return c;
    const t = extrapolate ? phase(now, this.received, this.remainder) : Math.min(1, phase(now, this.received, this.remainder));
    const extra = Math.min(MAX_EXTRAPOLATION, (t - 1) * STEP);
    const raw = t <= 1 ? vector(p.pos, c.pos, t) : ahead(c.pos, c.vel, extra);
    const { pos } = this.correction.apply(now, raw, undefined, Math.hypot(c.vel.x, c.vel.z));
    this.correction.shown = { pos, vel: c.vel, at: now, extrapolated: t > 1 };
    return { ...c, pos };
  }
}

/** Render pose from a single pair of adjacent fixed steps, including wheel and chassis motion. A late snapshot
 *  extrapolates the chassis along its velocity and turn rate (and the wheels' spin) for up to MAX_EXTRAPOLATION. */
export class VehicleInterpolation {
  private current: VehicleState | null = null;
  private previous: VehicleState | null = null;
  private tick = -1;
  private received = 0;
  private remainder = 0;
  private correction = new Correction();
  push(current: VehicleState | null, previous: VehicleState | null, tick: number, remainder: number, now: number) {
    if (tick === this.tick) return;
    this.tick = tick; this.received = now; this.remainder = remainder;
    this.current = current;
    const continuous = current && previous && current.wheels.length === previous.wheels.length &&
      Math.hypot(current.pos.x - previous.pos.x, current.pos.y - previous.pos.y, current.pos.z - previous.pos.z) < 2;
    this.previous = continuous ? previous : current;
    if (!continuous) { this.correction.reset(); this.correction.shown = null; }
    this.correction.arrived();
  }
  /** `extrapolate`: false while the simulation is paused (a late state is then simply the latest). */
  sample(now: number, extrapolate = true): VehicleState | null {
    const c = this.current, p = this.previous;
    if (!c || !p) return c;
    const t = extrapolate ? phase(now, this.received, this.remainder) : Math.min(1, phase(now, this.received, this.remainder));
    let pose: VehicleState;
    if (t <= 1) {
      pose = { ...c, pos: vector(p.pos, c.pos, t), rot: rotation(p.rot, c.rot, t),
        wheels: c.wheels.map((w, i) => ({ ...w, pos: vector(p.wheels[i].pos, w.pos, t),
          steer: angle(p.wheels[i].steer, w.steer, t), rotation: angle(p.wheels[i].rotation, w.rotation, t) })) };
    } else {
      // The last step's turn and wheel spin continue at the same rate; wheel positions are chassis-local.
      const steps = Math.min(MAX_EXTRAPOLATION, (t - 1) * STEP) / STEP;
      const turn = multiply(c.rot, conjugate(p.rot));
      pose = { ...c, pos: ahead(c.pos, c.linvel, steps * STEP), rot: rotation(c.rot, multiply(scaleTurn(turn, steps), c.rot), 1),
        wheels: c.wheels.map((w, i) => ({ ...w, rotation: w.rotation + Math.atan2(Math.sin(w.rotation - p.wheels[i].rotation), Math.cos(w.rotation - p.wheels[i].rotation)) * steps })) };
    }
    const corrected = this.correction.apply(now, pose.pos, pose.rot, c.speed);
    this.correction.shown = { pos: corrected.pos, rot: corrected.rot, vel: c.linvel, at: now, extrapolated: t > 1 };
    return { ...pose, pos: corrected.pos, rot: corrected.rot ?? pose.rot };
  }
}
