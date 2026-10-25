import type { CharacterState, Quat, VehicleState } from '../physics/protocol';
const STEP = 1 / 60;
const alpha = (now: number, received: number, remainder: number) => Math.min(1, Math.max(0, (now - received + remainder) / STEP));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const angle = (a: number, b: number, t: number) => a + Math.atan2(Math.sin(b - a), Math.cos(b - a)) * t;
const vector = (a: { x: number; y: number; z: number }, b: { x: number; y: number; z: number }, t: number) =>
  ({ x: lerp(a.x, b.x, t), y: lerp(a.y, b.y, t), z: lerp(a.z, b.z, t) });
function rotation(a: Quat, b: Quat, t: number): Quat {
  const sign = a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w < 0 ? -1 : 1;
  const q = { x: lerp(a.x, b.x * sign, t), y: lerp(a.y, b.y * sign, t),
    z: lerp(a.z, b.z * sign, t), w: lerp(a.w, b.w * sign, t) };
  const length = Math.hypot(q.x, q.y, q.z, q.w) || 1;
  return { x: q.x / length, y: q.y / length, z: q.z / length, w: q.w / length };
}
/** One fixed-step of render latency; packets with no new physics tick never reset the clock. */
export class CharacterInterpolation {
  private current: CharacterState | null = null;
  private previous: CharacterState | null = null;
  private tick = -1;
  private received = 0;
  private remainder = 0;
  push(current: CharacterState | null, previous: CharacterState | null, tick: number, remainder: number, now: number) {
    if (tick === this.tick) return;
    this.tick = tick; this.received = now; this.remainder = remainder;
    this.current = current;
    this.previous = previous;
    if (current && previous && (current.enabled !== previous.enabled ||
      Math.hypot(current.pos.x - previous.pos.x, current.pos.y - previous.pos.y, current.pos.z - previous.pos.z) > 2)) this.previous = current;
  }
  sample(now: number): CharacterState | null {
    const c = this.current, p = this.previous;
    if (!c || !p) return c;
    const t = alpha(now, this.received, this.remainder);
    return { ...c, pos: vector(p.pos, c.pos, t) };
  }
}

/** Render pose from a single pair of adjacent fixed steps, including wheel and chassis motion. */
export class VehicleInterpolation {
  private current: VehicleState | null = null;
  private previous: VehicleState | null = null;
  private tick = -1;
  private received = 0;
  private remainder = 0;
  push(current: VehicleState | null, previous: VehicleState | null, tick: number, remainder: number, now: number) {
    if (tick === this.tick) return;
    this.tick = tick; this.received = now; this.remainder = remainder;
    this.current = current;
    this.previous = current && previous && current.wheels.length === previous.wheels.length &&
      Math.hypot(current.pos.x - previous.pos.x, current.pos.y - previous.pos.y, current.pos.z - previous.pos.z) < 2
      ? previous : current;
  }
  sample(now: number): VehicleState | null {
    const c = this.current, p = this.previous;
    if (!c || !p) return c;
    const t = alpha(now, this.received, this.remainder);
    return { ...c, pos: vector(p.pos, c.pos, t), rot: rotation(p.rot, c.rot, t),
      wheels: c.wheels.map((w, i) => ({ ...w, pos: vector(p.wheels[i].pos, w.pos, t),
        steer: angle(p.wheels[i].steer, w.steer, t), rotation: angle(p.wheels[i].rotation, w.rotation, t) })) };
  }
}
