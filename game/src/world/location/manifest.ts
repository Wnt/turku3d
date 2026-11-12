// The location manifest of the world pipeline (docs/design/world-pipeline.md, section 5): what the client loads.

export interface LocationInstances {
  /** A GLB: a store object (relative to the store root) or a path under the game's assets. */
  asset: string;
  /** The asset's own box (minX, minY, minZ, maxX, maxY, maxZ) when the pipeline packed it; one object's bounds with its transform. */
  bounds?: number[];
  /** x, y, z, yaw, sx, sy, sz per object, relative to the entity's origin; the asset's origin is the object's base. */
  transforms: number[][];
  /** One stable id per transform, for objects the game addresses. */
  ids?: string[];
  /** What a hit does to the object (the stills only carry it). */
  behaviour?: 'fixed' | 'knock-over' | 'breakable';
  /** Asset-local, metres, base at y = 0. */
  collision?: { shape: 'box' | 'capsule' | 'cylinder'; size: number[] };
  /** Kilograms of one object. */
  mass?: number;
}

export interface LocationEntity {
  id: string;
  kind: string;
  tier: string;
  role: 'scene' | 'background';
  /** Content hash: the entity is redrawn exactly when it changes. */
  hash: string;
  /** Engine metres, absolute. */
  origin: [number, number, number];
  /** minX, minY, minZ, maxX, maxY, maxZ relative to `origin`. */
  bounds: number[];
  lod?: number;
  url?: string;
  bytes?: number;
  triangles?: number;
  instances: LocationInstances[];
}

export interface LocationManifest {
  schema: 'turku-location/1';
  name: string;
  revision: number;
  built: string;
  query: { crs: string; at: [number, number]; radius: number; background: number };
  origin: [number, number, number];
  focus: [number, number, number];
  entities: LocationEntity[];
  stats: { entities: number; triangles: number; bytes: number; instances: number; byKind: Record<string, number> };
  timings: Record<string, number>;
}

const fail = (why: string): never => { throw new Error(`Invalid location manifest: ${why}`); };
const numbers = (value: unknown, length: number) => Array.isArray(value) && value.length === length && value.every(Number.isFinite);

/** Checks what the loader relies on; unknown fields pass (the pipeline may carry more than the client draws). */
export function readLocation(value: unknown): LocationManifest {
  const data = value as LocationManifest;
  if (!data || data.schema !== 'turku-location/1') fail('not a turku-location/1 document');
  if (!/^[a-z0-9-]+$/.test(data.name ?? '') || !Number.isInteger(data.revision)) fail('name and revision are required');
  if (!numbers(data.origin, 3) || !numbers(data.focus, 3) || !Array.isArray(data.entities)) fail('origin, focus and entities are required');
  const ids = new Set<string>();
  for (const row of data.entities) {
    if (!/^[a-z][a-z0-9-]*\/[A-Za-z0-9._-]+$/.test(row.id ?? '') || ids.has(row.id)) fail(`bad or repeated entity id ${row.id}`);
    ids.add(row.id);
    if (typeof row.hash !== 'string' || !numbers(row.origin, 3) || !numbers(row.bounds, 6)) fail(`${row.id}: hash, origin and bounds are required`);
    if (row.url !== undefined && !/^objects\/[a-f0-9]{2}\/[a-f0-9]{64}\.glb$/.test(row.url)) fail(`${row.id}: mesh url is not a store object`);
    if (!Array.isArray(row.instances)) fail(`${row.id}: instances are required`);
    for (const item of row.instances) {
      if (typeof item.asset !== 'string' || !Array.isArray(item.transforms) || !item.transforms.every(t => numbers(t, 7))) fail(`${row.id}: bad instance list`);
      if (item.ids && item.ids.length !== item.transforms.length) fail(`${row.id}: instance ids do not match the transforms`);
    }
  }
  return data;
}
