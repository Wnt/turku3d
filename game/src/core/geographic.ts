/** EPSG:3877 (ETRS89 / GK23FIN) inverse Transverse Mercator on GRS80.
 * Uses the conventional ETRS89-to-WGS84 identity mapping (no epoch correction). */
export function enToLatLon(E: number, N: number): { lat: number; lon: number } {
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
  const t = tan * tan, c = ep2 * cos * cos, d = (E - 23500000) / n;
  const lat = phi - n * tan / r * (d ** 2 / 2 - (5 + 3 * t + 10 * c - 4 * c * c - 9 * ep2) * d ** 4 / 24
    + (61 + 90 * t + 298 * c + 45 * t * t - 252 * ep2 - 3 * c * c) * d ** 6 / 720);
  const lon = (d - (1 + 2 * t + c) * d ** 3 / 6
    + (5 - 2 * c + 28 * t - 3 * c * c + 8 * ep2 + 24 * t * t) * d ** 5 / 120) / cos;
  return { lat: lat * 180 / Math.PI, lon: 23 + lon * 180 / Math.PI };
}
