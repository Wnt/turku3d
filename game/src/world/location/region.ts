// A region of the world pipeline (docs/design/world-regions.md): one continuous world built in 500 m units, whose
// index lists one content-addressed chunk object per 250 m square. The location loader draws a region as it draws a
// location, with the set of chunks following the camera: chunks within a layer's range are fetched nearest first
// and drawn, chunks beyond range + slack are released. Collision cells are listed by url and the game's collision
// streamer loads the ones near the player.
import { readLocation, type LocationEntity, type LocationManifest } from './manifest';

export interface RegionChunk { url: string; sha256: string; bytes: number; entities: number; triangles: number; y: [number, number] }

export interface RegionIndex {
  schema: 'turku-region/1';
  name: string;
  revision: number;
  built: string;
  origin: [number, number, number];
  centre: [number, number];
  focus: [number, number, number];
  /** Side of a chunk square, metres (250). */
  chunk: number;
  /** `i_j` (i east, j south, as `floor(origin / chunk)` of the rows inside) -> its chunk object. */
  chunks: Record<string, RegionChunk>;
  collision?: LocationManifest['collision'];
  roads?: LocationManifest['roads'];
  start?: LocationManifest['start'];
  units: { id: string; square: number[]; revision: number; built: string; entities: number }[];
  stats: LocationManifest['stats'];
}

/** Metres from the camera to which a renderer layer's chunks are drawn (ground and buildings to the horizon of the
 *  region, trees at their far mesh beyond the near range, street objects only near). */
export const REGION_RANGE: Record<string, number> = { ground: 700, buildings: 700, vegetation: 600, structures: 300 };
/** A drawn chunk stays until it is this much farther than its range, so a player at a border does not reload it. */
export const REGION_SLACK = 150;
/** The camera moves this far (m) before the wanted chunks are looked for again. */
export const REGION_STEP = 40;

const fail = (why: string): never => { throw new Error(`Invalid region index: ${why}`); };

export function isRegion(value: unknown): value is RegionIndex {
  return (value as { schema?: string })?.schema === 'turku-region/1';
}

export function readRegion(value: unknown): RegionIndex {
  const data = value as RegionIndex;
  if (!isRegion(data)) fail('not a turku-region/1 document');
  if (!/^[a-z0-9-]+$/.test(data.name ?? '') || !Number.isInteger(data.revision)) fail('name and revision are required');
  if (!data.chunks || typeof data.chunks !== 'object' || !Object.keys(data.chunks).length) fail('no chunks');
  for (const [key, chunk] of Object.entries(data.chunks)) {
    if (!/^-?\d+_-?\d+$/.test(key) || !/^objects\/[a-f0-9]{2}\/[a-f0-9]{64}\.json$/.test(chunk.url ?? '')) fail(`bad chunk ${key}`);
  }
  // The rest (collision, roads, start, focus, origin) is what a location manifest carries: checked as one.
  readLocation(regionManifest(data));
  return data;
}

/** The location manifest the loader keeps for a region: its rows are those of the chunks drawn now. */
export function regionManifest(index: RegionIndex): LocationManifest {
  return {
    schema: 'turku-location/1', name: index.name, revision: index.revision, built: index.built,
    query: { crs: 'EPSG:3877', at: index.centre, radius: 0, background: 0 },
    origin: index.origin, focus: index.focus, entities: [], collision: index.collision, start: index.start, roads: index.roads,
    stats: index.stats, timings: {},
  };
}

/** The rows of one chunk object, checked as a location's rows are. */
export function readChunk(index: RegionIndex, key: string, value: unknown): LocationEntity[] {
  const data = value as { schema?: string; chunk?: number[]; entities?: LocationEntity[] };
  if (data?.schema !== 'turku-chunk/1' || `${data.chunk?.[0]}_${data.chunk?.[1]}` !== key) fail(`chunk ${key} is not its object`);
  return readLocation({ ...regionManifest(index), entities: data.entities }).entities;
}

/** Distance in metres on the ground from (x, z) to chunk `i_j`'s square; 0 inside it. */
export function chunkDistance(key: string, side: number, x: number, z: number) {
  const [i, j] = key.split('_').map(Number);
  return Math.hypot(Math.max(i * side - x, 0, x - (i + 1) * side), Math.max(j * side - z, 0, z - (j + 1) * side));
}
