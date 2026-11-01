// The single coupling point between street lights and the day cycle. The dusk/night work (local/night-look)
// exports nightFactor(timeOfDay); when it merges, this body becomes `return nightFactor(time)` and nothing else in
// render/lights changes. Until then the level follows the sun the sky actually shows (Environment.sunElevation),
// which today's daylight-only cycle keeps high: lights stay off unless ?time= or ?lights= says otherwise.

export interface NightInputs {
  /** City clock (DayClock.time). */
  time: Date;
  /** Elevation of the rendered sun, degrees (Environment.sunElevation). */
  sunElevation: number;
}

/** Artificial-light level 0 (day) to 1 (night): lights ramp on from +3 degrees down to civil twilight (-6). */
export function nightLevel({ sunElevation }: NightInputs): number {
  const t = Math.min(1, Math.max(0, (3 - sunElevation) / 9));
  return t * t * (3 - 2 * t);
}
