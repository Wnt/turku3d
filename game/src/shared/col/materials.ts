// GENERATED from docs/specs/materials.md by `python -m turku3d.col.materials --write`.
// Do not edit by hand; tests fail when this file is stale.

export const MATERIAL_TABLE_VERSION = 1;

export type CollisionClass = 'solid' | 'query' | 'sensor';

export const MAT_FLAG = {
  CURB: 1,
  STAIR: 2,
  NOWALK: 4,
  FIX_EDGES: 8,
} as const;

/** Rapier interaction-group bits (16-bit membership / filter), materials.md. */
export const GROUP = {
  STATIC_SOLID: 1,
  STATIC_QUERY: 2,
  STATIC_SENSOR: 4,
  CHARACTER: 16,
  VEHICLE: 32,
  DYNAMIC: 64,
} as const;

export interface MaterialInfo {
  id: number;
  name: string;
  cls: CollisionClass;
  friction: number;
  audio: string;
  /** Bit set of MAT_FLAG. */
  flags: number;
}

export const MATERIALS: readonly MaterialInfo[] = [
  { id: 0, name: 'unknown', cls: 'solid', friction: 0.8, audio: 'concrete', flags: 0 },
  { id: 1, name: 'road', cls: 'solid', friction: 0.9, audio: 'asphalt', flags: 0 },
  { id: 2, name: 'sidewalk', cls: 'solid', friction: 0.85, audio: 'concrete', flags: 0 },
  { id: 3, name: 'curb', cls: 'solid', friction: 0.85, audio: 'stone', flags: MAT_FLAG.CURB | MAT_FLAG.FIX_EDGES },
  { id: 4, name: 'curb_dropped', cls: 'solid', friction: 0.85, audio: 'stone', flags: MAT_FLAG.FIX_EDGES },
  { id: 5, name: 'crossing', cls: 'solid', friction: 0.9, audio: 'asphalt', flags: 0 },
  { id: 6, name: 'cycleway', cls: 'solid', friction: 0.9, audio: 'asphalt', flags: 0 },
  { id: 7, name: 'cobblestone', cls: 'solid', friction: 0.8, audio: 'stone', flags: 0 },
  { id: 8, name: 'paving_slab', cls: 'solid', friction: 0.85, audio: 'stone', flags: 0 },
  { id: 9, name: 'gravel', cls: 'solid', friction: 0.7, audio: 'gravel', flags: 0 },
  { id: 10, name: 'grass', cls: 'solid', friction: 0.6, audio: 'grass', flags: 0 },
  { id: 11, name: 'soil', cls: 'solid', friction: 0.6, audio: 'soil', flags: 0 },
  { id: 12, name: 'sand', cls: 'solid', friction: 0.5, audio: 'sand', flags: 0 },
  { id: 13, name: 'rock', cls: 'solid', friction: 0.8, audio: 'stone', flags: 0 },
  { id: 14, name: 'water', cls: 'sensor', friction: 0.0, audio: 'water', flags: 0 },
  { id: 15, name: 'quay', cls: 'solid', friction: 0.85, audio: 'stone', flags: MAT_FLAG.FIX_EDGES },
  { id: 16, name: 'stair_slope', cls: 'solid', friction: 0.9, audio: 'stone', flags: MAT_FLAG.STAIR },
  { id: 17, name: 'stair_tread_helper', cls: 'query', friction: 0.0, audio: 'stone', flags: MAT_FLAG.STAIR },
  { id: 18, name: 'ramp', cls: 'solid', friction: 0.9, audio: 'concrete', flags: 0 },
  { id: 19, name: 'wall', cls: 'solid', friction: 0.8, audio: 'concrete', flags: MAT_FLAG.NOWALK },
  { id: 20, name: 'fence', cls: 'solid', friction: 0.6, audio: 'metal', flags: MAT_FLAG.NOWALK },
  { id: 21, name: 'railing', cls: 'solid', friction: 0.6, audio: 'metal', flags: MAT_FLAG.NOWALK },
  { id: 22, name: 'building', cls: 'solid', friction: 0.8, audio: 'concrete', flags: MAT_FLAG.NOWALK },
  { id: 23, name: 'roof', cls: 'solid', friction: 0.8, audio: 'roof', flags: 0 },
  { id: 24, name: 'bridge_deck', cls: 'solid', friction: 0.9, audio: 'asphalt', flags: 0 },
  { id: 25, name: 'pole', cls: 'solid', friction: 0.6, audio: 'metal', flags: MAT_FLAG.NOWALK },
  { id: 26, name: 'tree_trunk', cls: 'solid', friction: 0.7, audio: 'wood', flags: MAT_FLAG.NOWALK },
  { id: 27, name: 'bollard', cls: 'solid', friction: 0.6, audio: 'metal', flags: MAT_FLAG.NOWALK },
  { id: 28, name: 'street_furniture', cls: 'solid', friction: 0.6, audio: 'metal', flags: 0 },
  { id: 29, name: 'vegetation_low', cls: 'solid', friction: 0.6, audio: 'grass', flags: 0 },
  { id: 30, name: 'metal_grate', cls: 'solid', friction: 0.8, audio: 'metal', flags: 0 },
  { id: 31, name: 'wood_deck', cls: 'solid', friction: 0.8, audio: 'wood', flags: 0 },
  { id: 32, name: 'invisible_boundary', cls: 'solid', friction: 0.0, audio: 'none', flags: MAT_FLAG.NOWALK },
];

export const MAT = {
  unknown: 0,
  road: 1,
  sidewalk: 2,
  curb: 3,
  curb_dropped: 4,
  crossing: 5,
  cycleway: 6,
  cobblestone: 7,
  paving_slab: 8,
  gravel: 9,
  grass: 10,
  soil: 11,
  sand: 12,
  rock: 13,
  water: 14,
  quay: 15,
  stair_slope: 16,
  stair_tread_helper: 17,
  ramp: 18,
  wall: 19,
  fence: 20,
  railing: 21,
  building: 22,
  roof: 23,
  bridge_deck: 24,
  pole: 25,
  tree_trunk: 26,
  bollard: 27,
  street_furniture: 28,
  vegetation_low: 29,
  metal_grate: 30,
  wood_deck: 31,
  invisible_boundary: 32,
} as const;

/** Collision class of a material id; unknown ids behave as solid (materials.md). */
export function materialClass(id: number): CollisionClass {
  return MATERIALS[id]?.cls ?? 'solid';
}

export function materialHasFlag(id: number, flag: number): boolean {
  return ((MATERIALS[id]?.flags ?? 0) & flag) !== 0;
}

/** 0 (unknown), unassigned ids and 200-255 must not appear in a shipped tile. */
export function isShippableMaterial(id: number): boolean {
  return id > 0 && id < MATERIALS.length;
}
