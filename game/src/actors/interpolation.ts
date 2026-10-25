import type { CharacterState } from '../physics/protocol';
const STEP = 1 / 60;
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
    const t = Math.min(1, Math.max(0, (now - this.received + this.remainder) / STEP));
    const lerp = (a: number, b: number) => a + (b - a) * t;
    return { ...c, pos: { x: lerp(p.pos.x, c.pos.x), y: lerp(p.pos.y, c.pos.y), z: lerp(p.pos.z, c.pos.z) } };
  }
}
