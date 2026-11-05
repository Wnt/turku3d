// World origin and frames (docs/ARCHITECTURE.md D3, docs/specs/tiles.md). Mirror of pipeline/turku3d/config.py.
// Engine frame: Y-up metres, x = E - E0, y = H (N2000), z = -(N - N0). E/N are the active city's grid (core/city.ts).
import { CITY, onCityChange } from './city';

// Live bindings: a city switch (core/city.ts setCity) rebases the engine origin to the new city's O, so engine
// coordinates stay small (float32 GPU and Rapier) wherever the city lies.
export let E0 = CITY.origin.E;
export let N0 = CITY.origin.N;
const H0 = 0;
export const VISUAL_L0 = 250;
export const COLLISION_TILE = 62.5;

/** Grid bearing (deg) of TRUE north at O, i.e. the grid's meridian convergence (Turku EPSG:3877: 0.640 deg at O,
 *  0.56 deg 7 km south-east). Engine/grid azimuth = true azimuth + this (sun, Street View headings, compass). */
export let TRUE_NORTH_GRID_BEARING_DEG = CITY.trueNorthGridBearingDeg;

/** The city's centre spawn (Turku: the office, Läntinen Rantakatu 15, the Phase 1 spawn). */
export let OFFICE_EN: { readonly E: number; readonly N: number } = { E: CITY.spawn.E, N: CITY.spawn.N };

onCityChange(city => {
  E0 = city.origin.E; N0 = city.origin.N;
  TRUE_NORTH_GRID_BEARING_DEG = city.trueNorthGridBearingDeg;
  OFFICE_EN = { E: city.spawn.E, N: city.spawn.N };
});

interface EN { E: number; N: number; H: number }
export interface V3 { x: number; y: number; z: number }

export function enToEngine(E: number, N: number, H = 0): V3 {
  return { x: E - E0, y: H - H0, z: -(N - N0) };
}
export function engineToEN(x: number, y: number, z: number): EN {
  return { E: x + E0, N: N0 - z, H: y + H0 };
}

export function collisionTileId(i: number, j: number): string { return `c_${i}_${j}`; }
export function collisionTileAt(x: number, z: number): { i: number; j: number } {
  return { i: Math.floor(x / COLLISION_TILE), j: Math.floor(z / COLLISION_TILE) };
}
/** Distance (m) from (x,z) to the footprint of collision tile (i,j). */
export function distToCollisionTile(x: number, z: number, i: number, j: number): number {
  const x0 = i * COLLISION_TILE, z0 = j * COLLISION_TILE;
  const dx = Math.max(x0 - x, 0, x - (x0 + COLLISION_TILE));
  const dz = Math.max(z0 - z, 0, z - (z0 + COLLISION_TILE));
  return Math.hypot(dx, dz);
}
export function parseCollisionId(id: string): { i: number; j: number } | null {
  const m = /^c_(-?\d+)_(-?\d+)$/.exec(id);
  return m ? { i: +m[1], j: +m[2] } : null;
}

/** Compass heading (deg, clockwise from north) -> engine yaw about +Y (rad) for a camera/actor whose forward is -Z at yaw 0. */
export function headingToYaw(headingDeg: number): number {
  return (-headingDeg * Math.PI) / 180;
}
export function yawToHeading(yaw: number): number {
  let h = (-yaw * 180) / Math.PI;
  h %= 360; if (h < 0) h += 360;
  return h;
}
