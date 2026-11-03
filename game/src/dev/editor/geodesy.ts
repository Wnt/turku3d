// Engine frame -> EPSG:3877 (ETRS-GK23, the game's grid, core/origin.ts) -> EPSG:3067 (ETRS-TM35FIN) for edit sessions.
// Transverse Mercator on GRS80 with the 4th-order Krüger series of JHS 154; sub-millimetre against pyproj in Turku.
import { engineToEN } from '../../core/origin';

const A = 6378137, F = 1 / 298.257222101;
const N = F / (2 - F), E = Math.sqrt(F * (2 - F));
const A1 = A / (1 + N) * (1 + N ** 2 / 4 + N ** 4 / 64);
const FORWARD = [N / 2 - 2 * N ** 2 / 3 + 5 * N ** 3 / 16 + 41 * N ** 4 / 180, 13 * N ** 2 / 48 - 3 * N ** 3 / 5 + 557 * N ** 4 / 1440,
  61 * N ** 3 / 240 - 103 * N ** 4 / 140, 49561 * N ** 4 / 161280];
const INVERSE = [N / 2 - 2 * N ** 2 / 3 + 37 * N ** 3 / 96 - N ** 4 / 360, N ** 2 / 48 + N ** 3 / 15 - 437 * N ** 4 / 1440,
  17 * N ** 3 / 480 - 37 * N ** 4 / 840, 4397 * N ** 4 / 161280];

interface Projection { lon0: number; k0: number; e0: number }
const GK23: Projection = { lon0: 23 * Math.PI / 180, k0: 1, e0: 23_500_000 };
const TM35FIN: Projection = { lon0: 27 * Math.PI / 180, k0: 0.9996, e0: 500_000 };

function toGeographic(p: Projection, east: number, north: number): { lat: number; lon: number } {
  const xi = north / (A1 * p.k0), eta = (east - p.e0) / (A1 * p.k0);
  let xi1 = xi, eta1 = eta;
  for (let k = 1; k <= 4; k++) {
    xi1 -= INVERSE[k - 1] * Math.sin(2 * k * xi) * Math.cosh(2 * k * eta);
    eta1 -= INVERSE[k - 1] * Math.cos(2 * k * xi) * Math.sinh(2 * k * eta);
  }
  const beta = Math.asin(Math.sin(xi1) / Math.cosh(eta1));
  const l = Math.asin(Math.tanh(eta1) / Math.cos(beta));
  const q = Math.asinh(Math.tan(beta));
  let q1 = q;
  for (let i = 0; i < 6; i++) q1 = q + E * Math.atanh(E * Math.tanh(q1));
  return { lat: Math.atan(Math.sinh(q1)), lon: p.lon0 + l };
}

function fromGeographic(p: Projection, lat: number, lon: number): { east: number; north: number } {
  const q = Math.asinh(Math.tan(lat)) - E * Math.atanh(E * Math.sin(lat));
  const beta = Math.atan(Math.sinh(q));
  const eta0 = Math.atanh(Math.cos(beta) * Math.sin(lon - p.lon0));
  const xi0 = Math.asin(Math.sin(beta) * Math.cosh(eta0));
  let xi = xi0, eta = eta0;
  for (let k = 1; k <= 4; k++) {
    xi += FORWARD[k - 1] * Math.sin(2 * k * xi0) * Math.cosh(2 * k * eta0);
    eta += FORWARD[k - 1] * Math.cos(2 * k * xi0) * Math.sinh(2 * k * eta0);
  }
  return { east: A1 * eta * p.k0 + p.e0, north: A1 * xi * p.k0 };
}

/** EPSG:3877 (E, N) -> EPSG:3067 (E, N). */
export function gk23ToTm35(east: number, north: number): { E: number; N: number } {
  const g = toGeographic(GK23, east, north);
  const t = fromGeographic(TM35FIN, g.lat, g.lon);
  return { E: t.east, N: t.north };
}

/** Grid convergence difference (deg): a yaw measured in EPSG:3877 grid azimuth plus this is the EPSG:3067 azimuth. */
export function tm35AzimuthOffsetDeg(east: number, north: number): number {
  const a = gk23ToTm35(east, north), b = gk23ToTm35(east, north + 10);
  return Math.atan2(b.E - a.E, b.N - a.N) * 180 / Math.PI;
}

const r = (x: number, d: number) => Math.round(x * 10 ** d) / 10 ** d;

/** One position in every frame an edit consumer needs: engine metres, EPSG:3877 and EPSG:3067 (both with N2000 H). */
export function framesOf(x: number, y: number, z: number) {
  const en = engineToEN(x, y, z);
  const tm = gk23ToTm35(en.E, en.N);
  return {
    engine: { x: r(x, 3), y: r(y, 3), z: r(z, 3) },
    epsg3877: { E: r(en.E, 3), N: r(en.N, 3), H: r(en.H, 3) },
    epsg3067: { E: r(tm.E, 3), N: r(tm.N, 3), H: r(en.H, 3) },
  };
}
