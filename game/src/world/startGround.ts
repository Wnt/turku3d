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
/** Horizontal boxes (engine x/z) a start must keep out of: street furniture, poles, parked cars. */
export interface Obstacle { minX: number; maxX: number; minZ: number; maxZ: number }

export interface StartOptions {
  /** The collision around `desired` is complete: the open-ground search may run. */
  open?: boolean;
  /** Furniture, poles and parked cars near the start (ground rays do not see props without collision). */
  obstacles?: readonly Obstacle[];
  /** The start view's horizontal forward: the chase camera sits behind the player along it, never inside the car. */
  view?: { dx: number; dz: number };
}

const CAR_HALF = { length: 2.6, width: 1.15 };
/** How far behind the player the first chase camera sits (m), with a margin. */
const CAMERA_BACK_M = 6.5;

const inBox = (b: Obstacle, x: number, z: number, margin: number) =>
  x >= b.minX - margin && x <= b.maxX + margin && z >= b.minZ - margin && z <= b.maxZ + margin;

/** Points over a car's footprint at (x, z, yaw), the same axes as supportedSpot (forward = (cos yaw, -sin yaw)). */
function carPoints(car: Point & { yaw: number }) {
  const c = Math.cos(car.yaw), s = Math.sin(car.yaw), out: Point[] = [];
  for (const u of [-1, -0.5, 0, 0.5, 1]) for (const v of [-1, 0, 1]) {
    out.push({ x: car.x + u * CAR_HALF.length * c + v * CAR_HALF.width * s, z: car.z - u * CAR_HALF.length * s + v * CAR_HALF.width * c });
  }
  return out;
}

/** Whether (x, z) lies in the car's footprint grown by `margin`. */
export function insideCar(car: Point & { yaw: number }, x: number, z: number, margin = 0) {
  const c = Math.cos(car.yaw), s = Math.sin(car.yaw), dx = x - car.x, dz = z - car.z;
  const along = dx * c - dz * s, across = dx * s + dz * c;
  return Math.abs(along) <= CAR_HALF.length + margin && Math.abs(across) <= CAR_HALF.width + margin;
}

/** Grid cell (m) of the obstacle index. */
const OBSTACLE_CELL = 4;
const obstacleIndexes = new WeakMap<readonly Obstacle[], Map<string, Obstacle[]>>();
/** Obstacles by 4 m cell (each box in every cell it touches), built once per list: the open-ground search tests
 *  thousands of spots against thousands of props. */
function indexOf(obstacles: readonly Obstacle[]) {
  let index = obstacleIndexes.get(obstacles);
  if (index) return index;
  index = new Map();
  for (const b of obstacles) {
    for (let i = Math.floor(b.minX / OBSTACLE_CELL); i <= Math.floor(b.maxX / OBSTACLE_CELL); i++)
      for (let j = Math.floor(b.minZ / OBSTACLE_CELL); j <= Math.floor(b.maxZ / OBSTACLE_CELL); j++) {
        const key = `${i},${j}`;
        let list = index.get(key);
        if (!list) index.set(key, list = []);
        list.push(b);
      }
  }
  obstacleIndexes.set(obstacles, index);
  return index;
}

/** No obstacle under the car's footprint (`car`) or within 0.45 m of the player's feet. */
export function spotClear(obstacles: readonly Obstacle[], spot: Point & { yaw: number }, car: boolean) {
  if (!obstacles.length) return true;
  const index = indexOf(obstacles);
  const points = car ? carPoints(spot) : [spot];
  const margin = car ? 0.2 : 0.45;
  for (const p of points) for (let di = -1; di <= 1; di++) for (let dj = -1; dj <= 1; dj++) {
    const near = index.get(`${Math.floor(p.x / OBSTACLE_CELL) + di},${Math.floor(p.z / OBSTACLE_CELL) + dj}`);
    if (near?.some(b => inBox(b, p.x, p.z, margin))) return false;
  }
  return true;
}

/** Whether the chase camera, behind `spawn` along the view, would sit in or look through the car. */
export function cameraInCar(spawn: Point, car: Point & { yaw: number }, view?: { dx: number; dz: number }) {
  if (!view) return false;
  const length = Math.hypot(view.dx, view.dz) || 1, bx = -view.dx / length, bz = -view.dz / length;
  for (let d = 0.5; d <= CAMERA_BACK_M; d += 0.5) if (insideCar(car, spawn.x + bx * d, spawn.z + bz * d, 0.5)) return true;
  return false;
}

export async function findInitialGround(desired: Point, graphs: readonly RoadGraphJson[], probe: GroundProbe,
  covers: (x: number, z: number, radius: number) => boolean, options: StartOptions = {}): Promise<{ spawn: Spot; car: Spot } | null> {
  const { open = false } = options;
  const memo = new Map<string, Promise<RayHit | null>>();
  const sample: GroundProbe = (x, z) => {
    const key = `${x},${z}`;
    if (!memo.has(key)) memo.set(key, probe(x, z));
    return memo.get(key)!;
  };
  const street = await streetStart(desired, graphs, sample, covers, options);
  const away = (start: { spawn: Spot } | null) => start ? Math.hypot(start.spawn.x - desired.x, start.spawn.z - desired.z) : Infinity;
  if (away(street) <= START_NEAR_M) return street;
  if (!open) return null;
  const ground = await openGround(desired, graphs, sample, covers, options);
  return away(ground) < away(street) ? ground : street;
}

/** The player stands within this distance (m) of the requested start, or the open-ground search is tried. */
export const START_NEAR_M = 15;

async function streetStart(desired: Point, graphs: readonly RoadGraphJson[], sample: GroundProbe,
  covers: (x: number, z: number, radius: number) => boolean, { obstacles = [], view }: StartOptions = {}): Promise<{ spawn: Spot; car: Spot } | null> {
  let fallback: { spawn: Spot; car: Spot } | null = null;
  for (const candidate of streetCandidates(desired, graphs)) {
    if (!covers(candidate.x, candidate.z, 3) || !spotClear(obstacles, candidate, true)) continue;
    const car = await supportedSpot(candidate, sample, true);
    if (!car) continue;
    // Nearest first, but a spot whose chase camera would sit in the car only if no other one is free.
    const walking = [-1, 1].flatMap(side => [3.2, 4.2, 5.2].map(distance => ({
      x: car.x + side * distance * Math.sin(car.yaw), z: car.z + side * distance * Math.cos(car.yaw), yaw: car.yaw,
    }))).sort((a, b) => Number(cameraInCar(a, car, view)) - Number(cameraInCar(b, car, view))
      || Math.hypot(a.x - desired.x, a.z - desired.z) - Math.hypot(b.x - desired.x, b.z - desired.z));
    for (const point of walking) {
      if (!covers(point.x, point.z, 0.5) || !spotClear(obstacles, point, false)) continue;
      const spawn = await supportedSpot(point, sample, false);
      if (!spawn) continue;
      if (!cameraInCar(spawn, car, view)) return { spawn, car };
      fallback ??= { spawn, car };
    }
  }
  return fallback;
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
  covers: (x: number, z: number, radius: number) => boolean, { obstacles = [], view }: StartOptions = {}) {
  const spawn = await nearestSpot(openPoints(desired).filter(p => covers(p.x, p.z, 0.5) && spotClear(obstacles, { ...p, yaw: 0 }, false)), async p =>
    safeSurface(await probe(p.x, p.z)) ? supportedSpot({ ...p, yaw: 0 }, probe, false) : null);
  if (!spawn) return null;
  const clear = (p: Point) => Math.hypot(p.x - spawn.x, p.z - spawn.z) >= 4 && covers(p.x, p.z, 3);
  const free = (spot: Spot | null) => spot && spotClear(obstacles, spot, true) && !cameraInCar(spawn, spot, view) ? spot : null;
  const streets = streetCandidates(spawn, graphs).filter(clear);
  const around = openPoints(spawn).filter(clear);
  const park = (drivable: boolean) => nearestSpot(around, async p => {
    if (!safeSurface(await probe(p.x, p.z), drivable)) return null;
    for (const yaw of CAR_YAWS) {
      const spot = free(await supportedSpot({ ...p, yaw }, probe, true, drivable));
      if (spot) return spot;
    }
    return null;
  });
  const car = await nearestSpot(streets, async p => free(await supportedSpot(p, probe, true))) ?? await park(true) ?? await park(false);
  return car ? { spawn, car } : null;
}
