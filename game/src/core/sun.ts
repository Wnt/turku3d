// Solar position (NOAA simplified algorithm, ~0.5 deg) for the Turku origin.
import { TRUE_NORTH_GRID_BEARING_DEG } from './origin';
const TURKU_LAT = 60.448;
const TURKU_LON = 22.267;

/** Returns azimuth (deg, clockwise from north) and elevation (deg) of the sun. */
export function sunPosition(date: Date, lat = TURKU_LAT, lon = TURKU_LON): { azimuth: number; elevation: number } {
  const rad = Math.PI / 180;
  const jd = date.getTime() / 86400000 + 2440587.5;
  const n = jd - 2451545.0;
  const L = (280.46 + 0.9856474 * n) % 360;
  const g = ((357.528 + 0.9856003 * n) % 360) * rad;
  const lambda = (L + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * rad;
  const eps = (23.439 - 0.0000004 * n) * rad;
  const ra = Math.atan2(Math.cos(eps) * Math.sin(lambda), Math.cos(lambda));
  const dec = Math.asin(Math.sin(eps) * Math.sin(lambda));
  const gmst = (18.697374558 + 24.06570982441908 * n) % 24;
  const lst = (gmst * 15 + lon) * rad;
  const ha = lst - ra;
  const phi = lat * rad;
  const el = Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(ha));
  const az = Math.atan2(-Math.sin(ha), Math.tan(dec) * Math.cos(phi) - Math.sin(phi) * Math.cos(ha));
  return { azimuth: ((az / rad) % 360 + 360) % 360, elevation: el / rad };
}

/** Unit vector towards the sun in engine axes (x east = grid east, y up, z grid south). `azimuth` is the TRUE
 *  azimuth; the vector is rotated by the meridian convergence because engine axes follow the EPSG:3877 grid. */
export function sunDirection(date: Date): { x: number; y: number; z: number; elevation: number; azimuth: number } {
  const { azimuth, elevation } = sunPosition(date);
  const a = ((azimuth + TRUE_NORTH_GRID_BEARING_DEG) * Math.PI) / 180, e = (elevation * Math.PI) / 180;
  return { x: Math.sin(a) * Math.cos(e), y: Math.sin(e), z: -Math.cos(a) * Math.cos(e), elevation, azimuth };
}

export type DayPhase = 'dawn' | 'noon' | 'dusk' | 'night';
export const DAY_PHASES: readonly DayPhase[] = ['dawn', 'noon', 'dusk', 'night'];
/** Dawn and dusk: the sun crosses this elevation (deg) rising and setting, low warm light. */
const TWILIGHT_ELEVATION = 4;
const STEP_MS = 5 * 60_000;

/**
 * The moment of `phase` on the calendar day of `date` in Turku (the 24 h from local midnight, UTC+2 standard time):
 * noon is the highest sun, night the lowest sun in the 12 h after it (the coming night), dawn and dusk the first and last time the sun stands at 4° above the
 * horizon. Where it never reaches that height (deep winter) dawn and dusk are three hours either side of noon.
 */
export function phaseTime(date: Date, phase: DayPhase): Date {
  const local = date.getTime() + 2 * 3_600_000;
  const start = local - (local % 86_400_000) - 2 * 3_600_000;
  const samples: { t: number; el: number }[] = [];
  for (let t = start; t < start + 86_400_000; t += STEP_MS) samples.push({ t, el: sunPosition(new Date(t)).elevation });
  const high = samples.reduce((a, b) => (b.el > a.el ? b : a));
  if (phase === 'noon') return new Date(high.t);
  if (phase === 'night') {
    let low = { t: high.t, el: high.el };
    for (let t = high.t; t <= high.t + 86_400_000 / 2; t += STEP_MS) {
      const el = sunPosition(new Date(t)).elevation;
      if (el < low.el) low = { t, el };
    }
    return new Date(low.t);
  }
  const crossing = phase === 'dawn'
    ? samples.find((s, i) => i > 0 && samples[i - 1].el < TWILIGHT_ELEVATION && s.el >= TWILIGHT_ELEVATION && s.t <= high.t)
    : [...samples].reverse().find((s, i, r) => i > 0 && r[i - 1].el < TWILIGHT_ELEVATION && s.el >= TWILIGHT_ELEVATION && s.t >= high.t);
  return new Date(crossing?.t ?? high.t + (phase === 'dawn' ? -3 : 3) * 3_600_000);
}
