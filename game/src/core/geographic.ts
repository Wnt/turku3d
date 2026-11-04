import { CITY, type CityProjection } from './city';

/** Grid E/N of the active city (core/city.ts) to WGS84 latitude/longitude (deg). */
export function enToLatLon(E: number, N: number, projection: CityProjection = CITY.projection): { lat: number; lon: number } {
  return projection.kind === 'rd' ? rdToLatLon(E, N) : gkToLatLon(E, N, projection.meridian);
}

/** Finnish GK zone `meridian` (EPSG:3873..3885, ETRS89 / GKnnFIN: k0 = 1, false easting meridian * 1e6 + 500 000):
 *  inverse Transverse Mercator on GRS80. Uses the conventional ETRS89-to-WGS84 identity mapping (no epoch correction). */
export function gkToLatLon(E: number, N: number, meridian: number): { lat: number; lon: number } {
  const a = 6378137, f = 1 / 298.257222101;
  const e2 = f * (2 - f), ep2 = e2 / (1 - e2);
  const e1 = (1 - Math.sqrt(1 - e2)) / (1 + Math.sqrt(1 - e2));
  const mu = N / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256));
  const phi = mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * Math.sin(2 * mu)
    + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * Math.sin(4 * mu)
    + 151 * e1 ** 3 / 96 * Math.sin(6 * mu) + 1097 * e1 ** 4 / 512 * Math.sin(8 * mu);
  const sin = Math.sin(phi), cos = Math.cos(phi), tan = Math.tan(phi);
  const n = a / Math.sqrt(1 - e2 * sin * sin);
  const r = a * (1 - e2) / (1 - e2 * sin * sin) ** 1.5;
  const t = tan * tan, c = ep2 * cos * cos, d = (E - (meridian * 1e6 + 500000)) / n;
  const lat = phi - n * tan / r * (d ** 2 / 2 - (5 + 3 * t + 10 * c - 4 * c * c - 9 * ep2) * d ** 4 / 24
    + (61 + 90 * t + 298 * c + 45 * t * t - 252 * ep2 - 3 * c * c) * d ** 6 / 720);
  const lon = (d - (1 + 2 * t + c) * d ** 3 / 6
    + (5 - 2 * c + 28 * t - 3 * c * c + 8 * ep2 + 24 * t * t) * d ** 5 / 120) / cos;
  return { lat: lat * 180 / Math.PI, lon: meridian + lon * 180 / Math.PI };
}

// RD New (EPSG:28992) to WGS84: the polynomial of Schreutelkamp and Strang van Hees (2001, "Benaderingsformules voor
// de transformatie tussen RD- en WGS84-kaartcoördinaten"), better than a metre inside the Netherlands. Rows are
// [p, q, coefficient] for dX^p dY^q with dX, dY in 100 km from Amersfoort.
const RD_LAT: readonly (readonly [number, number, number])[] = [
  [0, 1, 3235.65389], [2, 0, -32.58297], [0, 2, -0.2475], [2, 1, -0.84978], [0, 3, -0.0655], [2, 2, -0.01709],
  [1, 0, -0.00738], [4, 0, 0.0053], [2, 3, -0.00039], [4, 1, 0.00033], [1, 1, -0.00012]];
const RD_LON: readonly (readonly [number, number, number])[] = [
  [1, 0, 5260.52916], [1, 1, 105.94684], [1, 2, 2.45656], [3, 0, -0.81885], [1, 3, 0.05594], [3, 1, -0.05607],
  [0, 1, 0.01199], [3, 2, -0.00256], [1, 4, 0.00128], [0, 2, 0.00022], [2, 0, -0.00022], [5, 0, 0.00026]];

export function rdToLatLon(x: number, y: number): { lat: number; lon: number } {
  const dx = (x - 155000) * 1e-5, dy = (y - 463000) * 1e-5;
  const sum = (rows: typeof RD_LAT) => rows.reduce((s, [p, q, k]) => s + k * dx ** p * dy ** q, 0);
  return { lat: 52.1551744 + sum(RD_LAT) / 3600, lon: 5.38720621 + sum(RD_LON) / 3600 };
}
