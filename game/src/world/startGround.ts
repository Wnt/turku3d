// Initial actors must stand on supported streets, never a roof returned by a sky ray.
import { MAT, MATERIALS, MAT_FLAG } from '../shared/col/materials';
import { CAR, EDGE, NODE, ONEWAY, edgeLength, mergeRoadGraphs, parseRoadGraph, type RoadGraph, type RoadGraphJson } from '../gameplay/roadGraph';
import type { V3 } from '../core/origin';
import type { RayHit } from '../physics/protocol';

type Point = { x: number; z: number };
type Spot = V3 & { yaw: number };
export type GroundProbe = (x: number, z: number) => Promise<RayHit | null>;
/** A ray against static solid collision (physics raycast): clearance ahead of the car and along the start view. */
export type RayProbe = (origin: V3, dir: V3, maxToi: number) => Promise<RayHit | null>;
const DRIVABLE = new Set<number>([MAT.road, MAT.crossing, MAT.cobblestone, MAT.paving_slab, MAT.gravel, MAT.bridge_deck, MAT.ramp]);

/** A street point whose sky ray lands this far (m) from the street's own surface height is under a deck, a canopy or a
 *  building (a covered street, a building passage): the support rays would park the car on whatever covers it. */
export const STREET_LEVEL_M = 1.5;

/** Car road (m) behind a single connection below which that side is a pocket: a courtyard, a gated yard, a dead-end
 *  driveway or a parking aisle loop with one way in. Turku's office courtyard behind Läntinen Rantakatu 15 is ~115 m
 *  of service ways behind one gate in a fence. */
export const POCKET_M = 300;

/**
 * Car edges of `graph` in a pocket: cut off from the rest of the loaded road network by one bridge edge (an edge on no
 * cycle) whose far side holds less than POCKET_M of car road, or in a component that small. A street leaving the
 * loaded area (a seam node, NODE.SEAM) reaches the city's network, so the side that holds one is never a pocket.
 */
export function pocketEdges(graph: RoadGraph): Uint8Array {
  const n = graph.nodes.length / 2, out = n, pocket = new Uint8Array(graph.edges.length);
  // Arcs over car edges (edge index) and virtual seam links to `out` (negative ids).
  const adj: { to: number; edge: number }[][] = Array.from({ length: n + 1 }, () => []);
  graph.edges.forEach((e, i) => {
    if (!(e.flags & CAR) || e.a === e.b) return;
    adj[e.a].push({ to: e.b, edge: i }); adj[e.b].push({ to: e.a, edge: i });
  });
  let seams = 0;
  for (let v = 0; v < n; v++) if (graph.nodeFlags[v] & NODE.SEAM && adj[v].length) {
    const id = -1 - seams++;
    adj[v].push({ to: out, edge: id }); adj[out].push({ to: v, edge: id });
  }
  // Iterative DFS (Tarjan bridges). Each edge is owned by its deeper end, so a subtree's weight is the car road on
  // the child's side of the tree edge above it, that edge included.
  const tin = new Int32Array(n + 1).fill(-1), low = new Int32Array(n + 1), last = new Int32Array(n + 1);
  const parent = new Int32Array(n + 1).fill(-1), owner = new Int32Array(graph.edges.length).fill(-1);
  const rootOf = new Int32Array(n + 1), order: number[] = [];
  const bridges: { edge: number; child: number }[] = [];
  let timer = 0;
  for (const root of [out, ...Array.from({ length: n }, (_, i) => i)]) {
    if (tin[root] >= 0 || !adj[root].length) continue;
    tin[root] = low[root] = timer++; rootOf[root] = root; order.push(root);
    const stack = [{ v: root, via: Number.NaN, next: 0 }];
    while (stack.length) {
      const top = stack[stack.length - 1];
      if (top.next < adj[top.v].length) {
        const { to, edge } = adj[top.v][top.next++];
        if (edge === top.via) continue;
        if (tin[to] < 0) {
          tin[to] = low[to] = timer++; parent[to] = top.v; rootOf[to] = root; order.push(to);
          if (edge >= 0) owner[edge] = to;
          stack.push({ v: to, via: edge, next: 0 });
        } else {
          low[top.v] = Math.min(low[top.v], tin[to]);
          if (edge >= 0 && owner[edge] < 0) owner[edge] = tin[to] > tin[top.v] ? to : top.v;
        }
      } else {
        stack.pop();
        last[top.v] = timer - 1;
        const up = stack[stack.length - 1];
        if (up) {
          low[up.v] = Math.min(low[up.v], low[top.v]);
          if (low[top.v] > tin[up.v] && top.via >= 0) bridges.push({ edge: top.via, child: top.v });
        }
      }
    }
  }
  const weight = new Float64Array(n + 1);
  graph.edges.forEach((e, i) => { if (owner[i] >= 0) weight[owner[i]] += edgeLength(e); });
  for (let k = order.length - 1; k > 0; k--) { const v = order[k]; if (parent[v] >= 0) weight[parent[v]] += weight[v]; }
  const inside = (v: number, child: number) => tin[v] >= tin[child] && tin[v] <= last[child];
  const mark = (test: (ownerNode: number) => boolean) => owner.forEach((v, i) => { if (v >= 0 && test(v)) pocket[i] = 1; });
  for (const { edge, child } of bridges) {
    const root = rootOf[child], total = weight[root], near = weight[child];
    if (near < POCKET_M) mark(v => inside(v, child));
    // The root's side (the bridge included) of a component no seam reaches.
    if (root !== out && total - near + edgeLength(graph.edges[edge]) < POCKET_M) {
      mark(v => rootOf[v] === root && !inside(v, child)); pocket[edge] = 1;
    }
  }
  for (let v = 0; v < n; v++) if (rootOf[v] === v && tin[v] >= 0 && v !== out && weight[v] < POCKET_M) mark(u => rootOf[u] === v);
  return pocket;
}

/** One graph over the loaded tiles; a tile whose roads.json does not parse is left out rather than failing the start. */
function mergedRoads(graphs: readonly RoadGraphJson[]): RoadGraph {
  try { return mergeRoadGraphs(graphs); } catch {
    return mergeRoadGraphs(graphs.filter(g => { try { parseRoadGraph(g); return true; } catch { return false; } }));
  }
}

export type StreetCandidate = Point & { yaw: number; y?: number; pocket: boolean; oneway: boolean };

/** Points on CAR edges within `radius` of `at`, with the street's surface height `y` where the graph has heights:
 *  streets of the through network first (pocketEdges: never a courtyard or a dead-end driveway while a through street
 *  is in reach), nearest first within each. `yaw` (car axes) runs along the edge, its legal direction on a one-way.
 *  Tunnels and building passages (roads.json TUNNEL) are never starts: a start faces an open street. */
export function streetCandidates(at: Point, graphs: readonly RoadGraphJson[], radius = 80): StreetCandidate[] {
  const candidates = new Map<string, StreetCandidate>();
  const graph = mergedRoads(graphs), pockets = pocketEdges(graph);
  graph.edges.forEach((edge, index) => {
    if (!(edge.flags & CAR) || edge.eflags & EDGE.TUNNEL) return;
    const { points, heights } = edge, pocket = pockets[index] === 1, oneway = (edge.flags & ONEWAY) !== 0;
    for (let i = 0; i + 3 < points.length; i += 2) {
      const ax = points[i], az = points[i + 1], dx = points[i + 2] - ax, dz = points[i + 3] - az;
      const length = Math.hypot(dx, dz);
      if (length < 0.01) continue;
      const y0 = heights?.[i / 2], y1 = heights?.[i / 2 + 1];
      const projection = Math.max(0, Math.min(length, ((at.x - ax) * dx + (at.z - az) * dz) / length));
      for (const distance of [projection, ...Array.from({ length: Math.ceil(length / 3) + 1 }, (_, j) => Math.min(length, j * 3))]) {
        const x = ax + dx * distance / length, z = az + dz * distance / length;
        const y = y0 !== undefined && y1 !== undefined && Number.isFinite(y0) && Number.isFinite(y1) ? y0 + (y1 - y0) * distance / length : undefined;
        const key = `${x.toFixed(2)},${z.toFixed(2)}`;
        if (Math.hypot(x - at.x, z - at.z) > radius || (candidates.get(key) && !candidates.get(key)!.pocket)) continue;
        candidates.set(key, { x, z, yaw: Math.atan2(-dz, dx), ...(y !== undefined ? { y } : {}), pocket, oneway });
      }
    }
  });
  return [...candidates.values()].sort((a, b) => Number(a.pocket) - Number(b.pocket)
    || Math.hypot(a.x - at.x, a.z - at.z) - Math.hypot(b.x - at.x, b.z - at.z));
}

/** The support found over a street point is the street itself, not a deck, canopy or roof above it. */
const onStreetLevel = (street: { y?: number }, spot: { y: number }) => street.y === undefined || Math.abs(spot.y - street.y) <= STREET_LEVEL_M;

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
  /** The requested start view's horizontal forward: the chase camera sits behind the player along it, never inside
   *  the car. */
  view?: { dx: number; dz: number };
  /** Rays against static solid collision. With them the car faces the way along its street with open road ahead
   *  (CLEAR_AHEAD_M) and the player the requested view only where it is open (openView); without, both keep the
   *  street's direction and the requested view. */
  ray?: RayProbe;
}

/** Open road (m) the start car needs straight ahead of its front. */
export const CLEAR_AHEAD_M = 12;
/** Open view (m) the player needs ahead at eye height; a closer wall turns the start view (openView). */
export const OPEN_VIEW_M = 15;
/** How far the clearance and view rays look (m). */
const LOOK_M = 60;

/** Camera yaw (forward (-sin yaw, -cos yaw), core/origin headingToYaw) looking the way a car with `carYaw` faces. */
export const viewAlong = (carYaw: number) => Math.atan2(-Math.cos(carYaw), Math.sin(carYaw));
const viewDir = (yaw: number) => ({ dx: -Math.sin(yaw), dz: -Math.cos(yaw) });
const viewYaw = (view: { dx: number; dz: number }) => Math.atan2(-view.dx, -view.dz);

/** Open length (m) along the horizontal `dir` from `from`: the nearest solid that is not walkable ground rising ahead
 *  (normal.y >= 0.7: a ramp or a street climbing a hill). */
async function openLength(from: V3, dir: { dx: number; dz: number }, ray: RayProbe, heights: readonly number[], max = LOOK_M) {
  const hits = await Promise.all(heights.map(h => ray({ x: from.x, y: from.y + h, z: from.z }, { x: dir.dx, y: 0, z: dir.dz }, max)));
  return Math.min(max, ...hits.map(hit => hit && hit.normal.y < 0.7 ? hit.toi : max));
}

/** The car's heading along its street (car axes) with the most open road ahead of its front: either way on a
 *  two-way street, the legal one on a one-way. Rays at bumper height from the front's centre and both corners: the
 *  way is as open as its most open lane, so a pole or a bollard ahead does not count, a wall, a fence or a building
 *  across the road does. Null when no way has CLEAR_AHEAD_M. */
async function carHeading(car: Spot, street: StreetCandidate, ray: RayProbe): Promise<number | null> {
  let best: number | null = null, open = -1;
  for (const yaw of street.oneway ? [street.yaw] : [street.yaw, street.yaw > 0 ? street.yaw - Math.PI : street.yaw + Math.PI]) {
    const dx = Math.cos(yaw), dz = -Math.sin(yaw), sx = Math.sin(yaw), sz = Math.cos(yaw);
    const lengths = await Promise.all([-0.9, 0, 0.9].map(side => openLength({ x: car.x + dx * (CAR_HALF.length + 0.1) + sx * side,
      y: car.y, z: car.z + dz * (CAR_HALF.length + 0.1) + sz * side }, { dx, dz }, ray, [0.6])));
    const length = Math.max(...lengths);
    if (length > open) { open = length; best = yaw; }
  }
  return open >= CLEAR_AHEAD_M ? best : null;
}

/**
 * The start view (camera yaw) for a player at `at`: `yaw` when OPEN_VIEW_M ahead of the eyes is open, else the more
 * open way along the street (`along`, camera yaws), else the most open of 16 directions; never one that puts the
 * first chase camera in the car. A start never faces a wall or a fence a few metres away.
 */
export async function openView(at: V3, yaw: number, ray: RayProbe, { along = [], car }: { along?: readonly number[]; car?: Point & { yaw: number } } = {}) {
  const usable = (y: number) => !car || !cameraInCar(at, car, viewDir(y));
  const open = (y: number) => openLength(at, viewDir(y), ray, [1.6]);
  if (usable(yaw) && await open(yaw) >= OPEN_VIEW_M) return yaw;
  const best = async (yaws: readonly number[]) => {
    let pick: number | null = null, length = -1;
    for (const y of yaws.filter(usable)) { const l = await open(y); if (l > length) { length = l; pick = y; } }
    return { pick, length };
  };
  const street = await best(along);
  if (street.pick !== null && street.length >= OPEN_VIEW_M) return street.pick;
  const any = await best(Array.from({ length: 16 }, (_, k) => yaw + k * Math.PI / 8));
  return any.pick ?? street.pick ?? yaw;
}

/** Camera yaws along the street nearest to `at` (through streets first, within `radius`), both ways. */
export function streetViews(at: Point, graphs: readonly RoadGraphJson[], radius = 25) {
  const street = streetCandidates(at, graphs, radius)[0];
  return street ? [viewAlong(street.yaw), viewAlong(street.yaw + Math.PI)] : [];
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
  let start = street;
  if (away(street) > START_NEAR_M) {
    if (!open) return null;
    const ground = await openGround(desired, graphs, sample, covers, options);
    start = away(ground) < away(street) ? ground : street;
  }
  return start && withView(start, options);
}

/** The start's view as `spawn.yaw` (camera yaw): the requested view, turned to open street by openView when rays are
 *  given and it faces a wall. */
async function withView(start: { spawn: Spot; car: Spot }, { view, ray }: StartOptions) {
  const requested = view ? viewYaw(view) : viewAlong(start.car.yaw);
  const yaw = ray ? await openView(start.spawn, requested, ray, { along: [viewAlong(start.car.yaw), viewAlong(start.car.yaw + Math.PI)], car: start.car }) : requested;
  return { spawn: { ...start.spawn, yaw }, car: start.car };
}

/** The player stands within this distance (m) of the requested start, or the open-ground search is tried. */
export const START_NEAR_M = 15;

/** A car on `street` with its heading (carHeading), or null; a spot without open road ahead only as `weak`. */
async function parkOn(street: StreetCandidate, sample: GroundProbe, obstacles: readonly Obstacle[], ray?: RayProbe) {
  if (!spotClear(obstacles, street, true)) return null;
  const car = await supportedSpot(street, sample, true);
  if (!car || !onStreetLevel(street, car)) return null;
  if (!ray) return { car, weak: false };
  const yaw = await carHeading(car, street, ray);
  return yaw === null ? { car, weak: true } : { car: { ...car, yaw }, weak: false };
}

async function streetStart(desired: Point, graphs: readonly RoadGraphJson[], sample: GroundProbe,
  covers: (x: number, z: number, radius: number) => boolean, { obstacles = [], view, ray, open = false }: StartOptions = {}): Promise<{ spawn: Spot; car: Spot } | null> {
  let fallback: { spawn: Spot; car: Spot } | null = null, weak: { spawn: Spot; car: Spot } | null = null;
  // A street whose collision has not streamed in yet is not a rejected street: until the start collision is complete
  // (`open`), a pocket or a second-best spot never wins over it (a slow host once parked in the Turku office yard
  // because only the yard's collision cell had loaded).
  let loading = false;
  for (const candidate of streetCandidates(desired, graphs)) {
    if (candidate.pocket && loading && !open) return null;
    if (!covers(candidate.x, candidate.z, 3)) { loading = true; continue; }
    const parked = await parkOn(candidate, sample, obstacles, ray);
    if (!parked) continue;
    const { car } = parked;
    // Nearest first, but a spot whose chase camera would sit in the car only if no other one is free. With rays the
    // view is chosen after the spot (withView), so only the requested one without them decides here.
    const walking = [-1, 1].flatMap(side => [3.2, 4.2, 5.2].map(distance => ({
      x: car.x + side * distance * Math.sin(car.yaw), z: car.z + side * distance * Math.cos(car.yaw), yaw: car.yaw,
    }))).sort((a, b) => Number(!ray && cameraInCar(a, car, view)) - Number(!ray && cameraInCar(b, car, view))
      || Math.hypot(a.x - desired.x, a.z - desired.z) - Math.hypot(b.x - desired.x, b.z - desired.z));
    for (const point of walking) {
      if (!covers(point.x, point.z, 0.5) || !spotClear(obstacles, point, false)) continue;
      const spawn = await supportedSpot(point, sample, false);
      if (!spawn) continue;
      if (parked.weak) { weak ??= { spawn, car }; break; }
      if (ray || !cameraInCar(spawn, car, view)) return { spawn, car };
      fallback ??= { spawn, car };
    }
  }
  return loading && !open ? null : fallback ?? weak;
}

/** Distance (m) between the start cars of different players: two parked cars never touch. */
export const START_CAR_GAP_M = 6.5;

/**
 * Up to `count` start-car spots near `near`, apart from each other (START_CAR_GAP_M), in the order the start search
 * ranks them (through streets first, nearest first, open road ahead). Every player of a room computes the same start,
 * and a start car is local until it moves, so each player takes the spot of its own room slot: the cars never
 * interpenetrate when one of them is shared later.
 */
export async function startCars(near: Point, graphs: readonly RoadGraphJson[], probe: GroundProbe,
  covers: (x: number, z: number, radius: number) => boolean, count: number, { obstacles = [], ray }: StartOptions = {}): Promise<Spot[]> {
  const spots: Spot[] = [], weak: Spot[] = [];
  const apart = (p: Point) => [...spots, ...weak].every(s => Math.hypot(s.x - p.x, s.z - p.z) >= START_CAR_GAP_M);
  for (const candidate of streetCandidates(near, graphs)) {
    if (spots.length >= count) break;
    if (!covers(candidate.x, candidate.z, 3) || !apart(candidate)) continue;
    const parked = await parkOn(candidate, probe, obstacles, ray);
    if (parked) (parked.weak ? weak : spots).push(parked.car);
  }
  return [...spots, ...weak].slice(0, count);
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
 *  nearest supported CAR street point around the player (through streets first, open road ahead when rays are given),
 *  else the nearest flat street surface, else the nearest flat walkable ground (a car parked on a square). */
export async function openGround(desired: Point, graphs: readonly RoadGraphJson[], probe: GroundProbe,
  covers: (x: number, z: number, radius: number) => boolean, { obstacles = [], view, ray }: StartOptions = {}) {
  const spawn = await nearestSpot(openPoints(desired).filter(p => covers(p.x, p.z, 0.5) && spotClear(obstacles, { ...p, yaw: 0 }, false)), async p =>
    safeSurface(await probe(p.x, p.z)) ? supportedSpot({ ...p, yaw: 0 }, probe, false) : null);
  if (!spawn) return null;
  const clear = (p: Point) => Math.hypot(p.x - spawn.x, p.z - spawn.z) >= 4 && covers(p.x, p.z, 3);
  // With rays the view turns after parking (withView); without, the requested one keeps the camera out of the car.
  const free = (spot: Spot | null) => spot && spotClear(obstacles, spot, true) && (ray || !cameraInCar(spawn, spot, view)) ? spot : null;
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
  // In order: a street spot with open road ahead, then one without, then any flat street surface or ground.
  let weak: Spot | null = null;
  const onStreet = async (p: StreetCandidate) => {
    const parked = await parkOn(p, probe, obstacles, ray);
    const spot = parked && free(parked.car);
    if (spot && parked.weak) { weak ??= spot; return null; }
    return spot;
  };
  const car = await nearestSpot(streets, onStreet) ?? weak ?? await park(true) ?? await park(false);
  return car ? { spawn, car } : null;
}
