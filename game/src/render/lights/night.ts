// The single coupling point between street lights and the day cycle: lamps, posts and sprites follow the same
// night factor as the timecycle (sky, exposure, lit windows), from the elevation of the rendered sun.
import { nightFactor } from '../timecycle';

export interface NightInputs {
  /** City clock (DayClock.time). */
  time: Date;
  /** Elevation of the rendered sun, degrees (Environment.sunElevation). */
  sunElevation: number;
}

/** Artificial-light level 0 (day) to 1 (night), the timecycle's night factor. */
export function nightLevel({ sunElevation }: NightInputs): number {
  return nightFactor(sunElevation);
}
