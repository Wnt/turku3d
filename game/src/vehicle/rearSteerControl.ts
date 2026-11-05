// Player control of the 4WS rear steer (SPEC §3.5): a tap of `rearMode` (Q, Xbox X) cycles AUTO → CRAB → MANUAL →
// LOCK, holding it for 0.6 s returns to AUTO, and `rearRocker` (Z/X, right stick X) rocks the rear in MANUAL only.
import { nextRearMode, type RearSteerMode } from './rearSteer.ts';

export const REAR_MODE_HOLD = 0.6;

export class RearSteerControl {
  mode: RearSteerMode = 'auto';
  private held = 0;
  private wasDown = false;
  private resetDone = false;

  /** Adopt the mode a vehicle already holds (entering a truck, or after its state reports a change). */
  sync(mode: RearSteerMode | undefined) { if (mode) this.mode = mode; this.held = 0; this.wasDown = false; this.resetDone = false; }

  /** One frame of the driver's input. `button` is the held `rearMode` action (0..1), `rocker` the `rearRocker` axis.
   *  Returns the input fields for the vehicle: the mode is sent every frame, so a slow physics tick never misses it. */
  update(dt: number, button: number, rocker: number): { rearMode: RearSteerMode; rearSteer: number } {
    const down = button > 0.5;
    if (down) {
      this.held += dt;
      if (this.held >= REAR_MODE_HOLD && !this.resetDone) { this.mode = 'auto'; this.resetDone = true; }
    } else if (this.wasDown) {
      if (!this.resetDone) this.mode = nextRearMode(this.mode);
      this.held = 0; this.resetDone = false;
    }
    this.wasDown = down;
    return { rearMode: this.mode, rearSteer: this.mode === 'manual' ? Math.max(-1, Math.min(1, rocker)) : 0 };
  }

  /** Right-stick X belongs to the rocker, not the camera orbit, while MANUAL holds. */
  get rocking() { return this.mode === 'manual'; }
}

/** HUD tag of a rear mode. */
export const REAR_MODE_TAG: Record<RearSteerMode, string> = { auto: 'REAR AUTO', crab: 'REAR CRAB', manual: 'REAR MAN', lock: 'REAR LOCK' };
