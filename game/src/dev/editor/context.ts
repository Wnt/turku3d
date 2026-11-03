// Automatic context of an edit position, measured from what the game has loaded: the collision surface under it, the
// nearest road / kerb / sidewalk / grass surfaces around it (a ring of downward rays), the nearest facade, wall or
// fence (horizontal rays), the nearest car road and footpath of the road graph (distance, side, carriageway edge), the
// nearby props, and LiDAR evidence when the point-cloud overlay answers. All distances in metres, bearings in degrees
// clockwise from grid north (EPSG:3877; EPSG:3067 azimuths add tm35AzimuthOffsetDeg).
import { MATERIALS } from '../../shared/col/materials';
import type { RoadGraphJson } from '../../gameplay/roadGraph';
import type { V3 } from './catalog';
import type { PointCloudOverlay } from './pointCloudHook';

export interface RayHit { toi: number; point: V3; material: number }
export type Raycast = (origin: V3, dir: V3, far: number) => Promise<RayHit | null>;

/** Thin things standing on the ground: a downward probe looks through them for the surface. */
export const STANDING = new Set(['street_furniture', 'pole', 'bollard', 'tree_trunk', 'vegetation_low']);
// 12 × 12 downward probes and 12 horizontal ones per position: one short batch in the physics worker per commit.
const RINGS = [0.25, 0.5, 0.75, 1, 1.5, 2, 2.5, 3, 4, 6, 8, 10];
const DIRECTIONS = 12;
const HORIZONTAL = 30;

const r2 = (x: number) => Math.round(x * 100) / 100;
export const bearing = (dx: number, dz: number) => r2((Math.atan2(dx, -dz) * 180 / Math.PI + 360) % 360);
const name = (material: number) => MATERIALS[material]?.name ?? `material_${material}`;

export interface Near { distance_m: number; bearing_deg: number }
export interface RoadNear {
  edge_id: string | null; way: number | null; class: string; car: boolean; foot: boolean; width_m: number | null;
  /** Distance to the centreline, and to the carriageway edge (centreline - width / 2; negative: on the carriageway). */
  centreline_m: number; edge_m: number | null;
  /** Side of the edge's digitised direction the position is on, and the bearing of that direction there. */
  side: 'left' | 'right'; direction_deg: number; bearing_deg: number;
}
export interface Context {
  surface: string | null; surface_y: number | null;
  /** Nearest downward-probe hit of each surface material within 10 m (12 directions × 12 rings). */
  surfaces: Record<string, Near>;
  /** Nearest horizontal-probe hit (1.2 m above the ground, 12 directions, 30 m) of each solid material. */
  obstacles: Record<string, Near>;
  road: RoadNear | null; path: RoadNear | null;
  props: { type: string; object_id: string; distance_m: number; bearing_deg: number }[];
  lidar: unknown;
}

/** The ground surface under (x, z) near `y`: the first hit that is not a thin standing object. */
export async function groundUnder(raycast: Raycast, x: number, y: number, z: number, from = 2, far = 12): Promise<RayHit | null> {
  let top = y + from, reach = far;
  for (let i = 0; i < 4; i++) {
    const hit = await raycast({ x, y: top, z }, { x: 0, y: -1, z: 0 }, reach);
    if (!hit || !STANDING.has(name(hit.material))) return hit;
    top = hit.point.y - 0.02; reach = Math.max(0.5, reach - hit.toi);
  }
  return null;
}

const CLASSES = ['path', 'service', 'street', 'major'];

/** Nearest car road and footpath edges of the resident road graphs (engine-xz polylines). */
export function nearestRoads(graphs: readonly RoadGraphJson[], x: number, z: number): { road: RoadNear | null; path: RoadNear | null } {
  let road: (RoadNear & { d: number }) | null = null, path: (RoadNear & { d: number }) | null = null;
  for (const g of graphs) {
    const widthColumn = g.edgeAttrColumns?.indexOf('width') ?? -1, wayColumn = g.edgeAttrColumns?.indexOf('way') ?? -1;
    g.edges.forEach((row, i) => {
      const bits = row[2] ?? 0, foot = !!(bits & 1), car = !!(bits & 2);
      let best = Infinity, bx = 0, bz = 0, sx = 0, sz = 0, cross = 0;
      for (let k = 4; k + 3 < row.length; k += 2) {
        const ax = row[k], az = row[k + 1], cx = row[k + 2], cz = row[k + 3];
        const dx = cx - ax, dz = cz - az, l2 = dx * dx + dz * dz;
        const t = l2 > 0 ? Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / l2)) : 0;
        const px = ax + dx * t, pz = az + dz * t, d = Math.hypot(x - px, z - pz);
        if (d < best) { best = d; bx = px; bz = pz; sx = dx; sz = dz; cross = dx * (z - az) - dz * (x - ax); }
      }
      if (!Number.isFinite(best)) return;
      const width = widthColumn >= 0 ? g.edgeAttrs?.[i]?.[widthColumn] ?? null : null;
      // Engine z points south: a positive x/z cross product is the right-hand side of the direction.
      const near: RoadNear & { d: number } = { d: best, edge_id: g.edgeIds?.[i] ?? null, way: wayColumn >= 0 ? g.edgeAttrs?.[i]?.[wayColumn] ?? null : null,
        class: CLASSES[(bits >> 3) & 3] ?? 'path', car, foot, width_m: width, centreline_m: r2(best),
        edge_m: width === null ? null : r2(best - width / 2), side: cross > 0 ? 'right' : 'left',
        direction_deg: bearing(sx, sz), bearing_deg: bearing(bx - x, bz - z) };
      if (car && (!road || best < road.d)) road = near;
      if (foot && !car && (!path || best < path.d)) path = near;
    });
  }
  const strip = (n: (RoadNear & { d: number }) | null): RoadNear | null => { if (!n) return null; const { d: _, ...rest } = n; return rest; };
  return { road: strip(road), path: strip(path) };
}

export interface ContextHosts {
  raycast: Raycast;
  roads: () => readonly RoadGraphJson[];
  props: (x: number, z: number, radius: number) => { type: string; objectId: string; key: string; base: V3 }[];
  pointCloud: () => PointCloudOverlay | null;
}

/** Context of a ground position. `self` (an editable key) is left out of the nearby props. */
export async function measureContext(hosts: ContextHosts, at: V3, self: string | null): Promise<Context> {
  const ground = await groundUnder(hosts.raycast, at.x, at.y, at.z);
  const y = ground?.point.y ?? at.y;
  const surfaces: Record<string, Near> = {}, obstacles: Record<string, Near> = {};
  const keep = (into: Record<string, Near>, material: string, dx: number, dz: number) => {
    const d = r2(Math.hypot(dx, dz));
    if (!into[material] || d < into[material].distance_m) into[material] = { distance_m: d, bearing_deg: bearing(dx, dz) };
  };
  const probes: Promise<void>[] = [];
  for (const radius of RINGS) for (let k = 0; k < DIRECTIONS; k++) {
    const a = (k / DIRECTIONS) * Math.PI * 2, dx = Math.sin(a) * radius, dz = -Math.cos(a) * radius;
    probes.push(hosts.raycast({ x: at.x + dx, y: y + 2, z: at.z + dz }, { x: 0, y: -1, z: 0 }, 6).then(hit => {
      if (hit && !STANDING.has(name(hit.material))) keep(surfaces, name(hit.material), dx, dz);
    }));
  }
  for (let k = 0; k < DIRECTIONS; k++) {
    const a = (k / DIRECTIONS) * Math.PI * 2, dir = { x: Math.sin(a), y: 0, z: -Math.cos(a) };
    probes.push(hosts.raycast({ x: at.x, y: y + 1.2, z: at.z }, dir, HORIZONTAL).then(hit => {
      if (hit) keep(obstacles, name(hit.material), dir.x * hit.toi, dir.z * hit.toi);
    }));
  }
  await Promise.all(probes);
  const roads = nearestRoads(hosts.roads(), at.x, at.z);
  const props = hosts.props(at.x, at.z, 15).filter(p => p.key !== self)
    .map(p => ({ type: p.type, object_id: p.objectId, distance_m: r2(Math.hypot(p.base.x - at.x, p.base.z - at.z)), bearing_deg: bearing(p.base.x - at.x, p.base.z - at.z) }))
    .sort((a, b) => a.distance_m - b.distance_m).slice(0, 8);
  let lidar: unknown = null;
  try { lidar = await hosts.pointCloud()?.queryNear?.(at.x, at.z, 2) ?? null; } catch (e) { lidar = { error: String(e) }; }
  return { surface: ground ? name(ground.material) : null, surface_y: ground ? r2(ground.point.y) : null,
    surfaces, obstacles, ...roads, props, lidar };
}
