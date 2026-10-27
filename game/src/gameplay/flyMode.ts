// Developer fly mode (localhost only, like Minecraft's creative flight): a double tap of jump or the Developer menu
// toggles it. While flying the character has no gravity; the move stick or keys move it in the camera's ground plane,
// flyUp / flyDown (jump or RT, B or LT or Q) move it vertically, and sprint is the fast modifier. The physics worker
// keeps the character's collisions (physics/world.ts, CharacterInput.fly), so it cannot pass through buildings.
import { SPEED } from '../actors/player';

export const FLY = {
  /** Flying speed (m/s), horizontal and vertical: three times the walking speed. */
  speed: 3 * SPEED.walk,
  /** With the sprint modifier: ten times the walking speed. */
  fastSpeed: 10 * SPEED.walk,
  /** Two jump presses within this many seconds toggle flight. */
  doubleTap: 0.35,
} as const;

/** What the frame loop gives the fly controller: the move direction in world x/z (length ≤ 1) and the held buttons. */
interface FlyInput { x: number; z: number; up: number; down: number; fast: boolean }

export class FlyMode {
  active = false;
  private lastTap = -Infinity;

  /** A jump press at `time` (s). Two within FLY.doubleTap toggle flight; returns whether this one did. */
  tap(time: number): boolean {
    if (time - this.lastTap <= FLY.doubleTap) {
      this.lastTap = -Infinity;
      this.active = !this.active;
      return true;
    }
    this.lastTap = time;
    return false;
  }

  set(active: boolean) {
    this.active = active;
    this.lastTap = -Infinity;
  }

  /** The character velocity (m/s, world axes) for this frame's input. */
  velocity(input: FlyInput): { vx: number; vy: number; vz: number } {
    const speed = input.fast ? FLY.fastSpeed : FLY.speed;
    const flat = Math.hypot(input.x, input.z);
    const scale = flat > 1 ? 1 / flat : 1;
    const climb = Math.max(-1, Math.min(1, input.up - input.down));
    return { vx: input.x * scale * speed, vy: climb * speed, vz: input.z * scale * speed };
  }
}
