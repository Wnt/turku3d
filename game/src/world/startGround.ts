// Initial actors must stand on supported streets, never a roof returned by a sky ray.
import { MAT, MATERIALS, MAT_FLAG } from '../shared/col/materials';
import { CAR, type RoadGraphJson } from '../gameplay/roadGraph';
import type { V3 } from '../core/origin';
import type { RayHit } from '../physics/protocol';

type Point = { x: number; z: number };
type Spot = V3 & { yaw: number };
export type GroundProbe = (x: number, z: number) => Promise<RayHit | null>;
const DRIVABLE = new Set<number>([MAT.road, MAT.crossing, MAT.cobblestone, MAT.paving_slab, MAT.gravel, MAT.bridge_deck, MAT.ramp]);

export function streetCandidates(at: Point, graphs: readonly RoadGraphJson[], radius = 80): (Point & { yaw: number })[] {
  const candidates = new Map<string, Point & { yaw: number }>();
  for (const graph of graphs) for (const edge of graph.edges) {
    if (!(edge[2] & CAR)) continue;
    const points = [graph.nodes[2 * edge[0]], graph.nodes[2 * edge[0] + 1], ...edge.slice(4),
      graph.nodes[2 * edge[1]], graph.nodes[2 * edge[1] + 1]];
    for (let i = 0; i + 3 < points.length; i += 2) {
      const ax = points[i], az = points[i + 1], dx = points[i + 2] - ax, dz = points[i + 3] - az;
      const length = Math.hypot(dx, dz);
      if (length < 0.01) continue;
      const projection = Math.max(0, Math.min(length, ((at.x - ax) * dx + (at.z - az) * dz) / length));
      for (const distance of [projection, ...Array.from({ length: Math.ceil(length / 3) + 1 }, (_, j) => Math.min(length, j * 3))]) {
        const x = ax + dx * distance / length, z = az + dz * distance / length;
        if (Math.hypot(x - at.x, z - at.z) <= radius) candidates.set(`${x.toFixed(2)},${z.toFixed(2)}`, { x, z, yaw: Math.atan2(-dz, dx) });
      }
    }
  }
  return [...candidates.values()].sort((a, b) => Math.hypot(a.x - at.x, a.z - at.z) - Math.hypot(b.x - at.x, b.z - at.z));
}

export function safeSurface(hit: RayHit | null, car = false): hit is RayHit {
  if (!hit || hit.normal.y < 0.85 || !Number.isFinite(hit.point.y)) return false;
  const material = MATERIALS[hit.material];
  return car ? DRIVABLE.has(hit.material) : !!material && material.cls === 'solid'
    && hit.material !== MAT.roof && hit.material !== MAT.unknown && !(material.flags & MAT_FLAG.NOWALK);
}

/** Nine support rays cover the chassis footprint, rejecting roofs, walls, water and steep kerbs. `drivable` (default:
 *  `car`) asks for street surfaces under it; a car parked on open ground passes `false` and stands on any walkable one. */
export async function supportedSpot(at: Point & { yaw: number }, probe: GroundProbe, car: boolean, drivable = car): Promise<Spot | null> {
  const length = car ? 2.6 : 0.4, width = car ? 1.15 : 0.4;
  const c = Math.cos(at.yaw), s = Math.sin(at.yaw);
  const hits = await Promise.all([-1, 0, 1].flatMap(u => [-1, 0, 1].map(v =>
    probe(at.x + u * length * c + v * width * s, at.z - u * length * s + v * width * c))));
  if (!hits.every(hit => safeSurface(hit, drivable))) return null;
  const heights = hits.map(hit => hit!.point.y);
  if (Math.max(...heights) - Math.min(...heights) > (car ? 0.35 : 0.25)) return null;
  return { ...at, y: heights[4] };
}

/**
 * The start next to `desired`: a car on the nearest supported CAR street and the player beside it (the CAR graph
 * limits parking candidates to mapped streets; support rays provide actual clearance). When that puts the player
 * more than START_NEAR_M from `desired`, or no street within reach supports a car (a pedestrian centre, a square,
 * streets under decks or canopies: Tapiola, Dam, Binnenrotte), and `open` says the collision around `desired` is
 * complete, the player stands on the nearest open walkable spot instead and the car parks around it (openGround).
 */
export async function findInitialGround(desired: Point, graphs: readonly RoadGraphJson[], probe: GroundProbe,
  covers: (x: number, z: number, radius: number) => boolean, open = false): Promise<{ spawn: Spot; car: Spot } | null> {
  const memo = new Map<string, Promise<RayHit | null>>();
  const sample: GroundProbe = (x, z) => {
    const key = `${x},${z}`;
    if (!memo.has(key)) memo.set(key, probe(x, z));
    return memo.get(key)!;
  };
  const street = await streetStart(desired, graphs, sample, covers);
  const away = (start: { spawn: Spot } | null) => start ? Math.hypot(start.spawn.x - desired.x, start.spawn.z - desired.z) : Infinity;
  if (away(street) <= START_NEAR_M) return street;
  if (!open) return null;
  const ground = await openGround(desired, graphs, sample, covers);
  return away(ground) < away(street) ? ground : street;
}

/** The player stands within this distance (m) of the requested start, or the open-ground search is tried. */
export const START_NEAR_M = 15;

async function streetStart(desired: Point, graphs: readonly RoadGraphJson[], sample: GroundProbe,
  covers: (x: number, z: number, radius: number) => boolean): Promise<{ spawn: Spot; car: Spot } | null> {
  for (const candidate of streetCandidates(desired, graphs)) {
    if (!covers(candidate.x, candidate.z, 3)) continue;
    const car = await supportedSpot(candidate, sample, true);
    if (!car) continue;
    const walking = [-1, 1].flatMap(side => [3.2, 4.2, 5.2].map(distance => ({
      x: car.x + side * distance * Math.sin(car.yaw), z: car.z + side * distance * Math.cos(car.yaw), yaw: car.yaw,
    }))).sort((a, b) => Math.hypot(a.x - desired.x, a.z - desired.z) - Math.hypot(b.x - desired.x, b.z - desired.z));
    for (const point of walking) {
      if (!covers(point.x, point.z, 0.5)) continue;
      const spawn = await supportedSpot(point, sample, false);
      if (spawn) return { spawn, car };
    }
  }
  return null;
}

/** Spacing (m) and reach (m) of the open-ground search; the reach matches the startup collision radius. */
const OPEN_STEP = 2, OPEN_RADIUS = 80;
/** Points evaluated together: nearest first, each batch in parallel. */
const OPEN_BATCH = 48;
const CAR_YAWS = [0, Math.PI / 2, Math.PI / 4, -Math.PI / 4];

/** Grid points within `radius` of `at`, nearest first. */
export function openPoints(at: Point, radius = OPEN_RADIUS, step = OPEN_STEP): Point[] {
  const out: Point[] = [];
  const n = Math.floor(radius / step);
  for (let i = -n; i <= n; i++) for (let j = -n; j <= n; j++) {
    if (Math.hypot(i, j) * step <= radius) out.push({ x: at.x + i * step, z: at.z + j * step });
  }
  return out.sort((a, b) => Math.hypot(a.x - at.x, a.z - at.z) - Math.hypot(b.x - at.x, b.z - at.z));
}

/** The first point (in order) whose `test` returns a spot; batches run in parallel but keep the nearest. */
async function nearestSpot<T>(points: readonly T[], test: (point: T) => Promise<Spot | null>): Promise<Spot | null> {
  for (let i = 0; i < points.length; i += OPEN_BATCH) {
    const found = (await Promise.all(points.slice(i, i + OPEN_BATCH).map(test))).find(spot => spot !== null);
    if (found) return found;
  }
  return null;
}

/** The player on the nearest open walkable spot to `desired` (a sky ray hits walkable ground there); the car on the
 *  nearest supported CAR street point around the player, else the nearest flat street surface, else the nearest
 *  flat walkable ground (a car parked on a square). */
export async function openGround(desired: Point, graphs: readonly RoadGraphJson[], probe: GroundProbe,
  covers: (x: number, z: number, radius: number) => boolean) {
  const spawn = await nearestSpot(openPoints(desired).filter(p => covers(p.x, p.z, 0.5)), async p =>
    safeSurface(await probe(p.x, p.z)) ? supportedSpot({ ...p, yaw: 0 }, probe, false) : null);
  if (!spawn) return null;
  const clear = (p: Point) => Math.hypot(p.x - spawn.x, p.z - spawn.z) >= 4 && covers(p.x, p.z, 3);
  const streets = streetCandidates(spawn, graphs).filter(clear);
  const around = openPoints(spawn).filter(clear);
  const park = (drivable: boolean) => nearestSpot(around, async p => {
    if (!safeSurface(await probe(p.x, p.z), drivable)) return null;
    for (const yaw of CAR_YAWS) {
      const spot = await supportedSpot({ ...p, yaw }, probe, true, drivable);
      if (spot) return spot;
    }
    return null;
  });
  const car = await nearestSpot(streets, p => supportedSpot(p, probe, true)) ?? await park(true) ?? await park(false);
  return car ? { spawn, car } : null;
}
