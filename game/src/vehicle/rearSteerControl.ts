// Player control of the 4WS rear steer (SPEC §3.5): in the trophy truck a press of `sprint` (Xbox A, Shift) cycles
// AUTO → AUTO 50% → LOCK → SIDEWAYS → AUTO. play.ts sends the held mode with every vehicle input and gives a press of
// A that also acts on a mission target (`mission` shares the button) to the mission instead.
import { nextRearMode, type RearSteerMode } from './rearSteer.ts';

export class RearSteerControl {
  mode: RearSteerMode = 'auto';

  /** Adopt the mode a vehicle already holds (entering a truck). */
  sync(mode: RearSteerMode | undefined) { if (mode) this.mode = mode; }

  /** One frame of the driver's input: `press` is a sprint press edge meant for the rear steer. The mode is returned
   *  every frame, so a slow physics tick never misses it. */
  update(press: boolean): RearSteerMode {
    if (press) this.mode = nextRearMode(this.mode);
    return this.mode;
  }
}

/** HUD names of the rear modes. */
export const REAR_MODE_LABEL: Record<RearSteerMode, string> = { auto: 'AUTO', auto50: 'AUTO 50%', lock: 'LOCK', crab: 'SIDEWAYS' };
