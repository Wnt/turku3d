// World origin and frames (docs/ARCHITECTURE.md D3, docs/specs/tiles.md). Mirror of pipeline/turku3d/config.py.
// Engine frame: Y-up metres, x = E - E0, y = H (N2000), z = -(N - N0).
export const E0 = 23459500;
export const N0 = 6704500;
export const H0 = 0;
export const CRS = 'EPSG:3877+3900';
export const VISUAL_L0 = 250;
export const COLLISION_TILE = 62.5;

/** Grid bearing (deg) of TRUE north at O, i.e. the EPSG:3877 meridian convergence (pyproj: 0.640 deg at O,
 *  0.56 deg 7 km south-east). Engine/grid azimuth = true azimuth + this (sun, Street View headings, compass). */
export const TRUE_NORTH_GRID_BEARING_DEG = 0.64;

/** Office, Läntinen Rantakatu 15 (Phase 1 spawn). */
export const OFFICE_EN = { E: 23459668, N: 6704254 } as const;

export interface EN { E: number; N: number; H: number }
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
