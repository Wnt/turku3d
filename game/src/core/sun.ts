// Solar position (NOAA simplified algorithm, ~0.5 deg) for the Turku origin.
import { TRUE_NORTH_GRID_BEARING_DEG } from './origin';
export const TURKU_LAT = 60.448;
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
export function sunVector(azimuth: number, elevation: number): { x: number; y: number; z: number } {
  const a = ((azimuth + TRUE_NORTH_GRID_BEARING_DEG) * Math.PI) / 180, e = (elevation * Math.PI) / 180;
  return { x: Math.sin(a) * Math.cos(e), y: Math.sin(e), z: -Math.cos(a) * Math.cos(e) };
}
