// dist/tiles/manifest.json (docs/specs/tiles.md §5).
import { E0, N0, COLLISION_TILE, VISUAL_L0 } from '../core/origin';

export interface ManifestEntry { url: string; sha256: string; bytes: number; tris: number; h: [number, number]; prims?: number; flags?: number; level?: number }
/** A draft parking slot and its car. `zone` is the slot class and `occ` its occupancy draw (0..1), which
 * world/parkedOccupancy.ts compares with the class's occupancy at the shared clock; `priority` 0 marks an
 * inferred kerb. Older drafts omit all three: their cars always show. */
export interface ParkedVehicle { object_id: string; model: string; color: string; x: number; y: number; z: number; yaw: number; width: number; height: number; length: number; clear_front_m: number; clear_rear_m: number;
  zone?: string; occ?: number; priority?: number }
export interface Manifest {
  format: string; version: number; built: string; code: string;
  origin_en: [number, number, number]; crs: string;
  grid: { visual_l0: number; collision: number; levels: number };
  materials_version: number; col_version: [number, number];
  visual: Record<string, ManifestEntry>; collision: Record<string, ManifestEntry>;
  spawn?: { position: [number, number, number]; headingDeg: number; vehicle: { position: [number, number, number]; yaw: number } };
  vehicles?: ParkedVehicle[];
  vehicle_models?: Record<string, { url: string; sha256: string; bytes: number }>;
  roads?: { url: string; sha256: string; bytes: number; nodes: number; edges: number };
  /** tileset.json of the same release. */
  tileset?: { url: string; sha256: string; bytes: number };
  /** Delivery address pool (pipeline/turku3d/content, format turku3d-addresses v1). */
  addresses?: { url: string; sha256: string; bytes: number; count: number };
  baseUrl: string;
}

/** Mutable residency inventory filled only by validated draft fragments. No published tile request. */
export function draftManifest(baseUrl: string): Manifest {
  return { format: 'turku3d-manifest', version: 1, built: '', code: '',
    origin_en: [E0, N0, 0], crs: 'EPSG:3877',
    grid: { visual_l0: VISUAL_L0, collision: COLLISION_TILE, levels: 1 },
    materials_version: 1, col_version: [1, 0], visual: {}, collision: {}, baseUrl };
}
