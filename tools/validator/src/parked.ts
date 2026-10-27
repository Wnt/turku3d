// Boxed-in parked cars: pipeline/turku3d/placement/manoeuvre.json measured against shipped solids.
// The rule matches turku3d.placement.clearance: the footprint plus SIDE metres on each side must be
// clear, and so must an exit lane of that width reaching AXIAL metres ahead of or behind the car.
import { readFileSync } from 'node:fs';
import { MATERIALS, PRIM_KIND } from '../../../game/src/shared/col/index.ts';
import type { ColTile } from '../../../game/src/shared/col/index.ts';

const ENVELOPE = JSON.parse(readFileSync(new URL('../../../pipeline/turku3d/placement/manoeuvre.json', import.meta.url), 'utf8')) as {
  side_m: number; axial_m: number; step_m: number; obstacle_materials: string[];
};
const OBSTACLE = new Set(MATERIALS.filter(m => ENVELOPE.obstacle_materials.includes(m.name)).map(m => m.id));
const r3 = (v: number) => Math.round(v * 1000) / 1000;

export interface ParkedCar { object_id: string; x: number; y: number; z: number; yaw: number; width: number; height: number; length: number }
type Tile = Pick<ColTile, 'header' | 'positions' | 'indices' | 'materials' | 'primitives'>;
/** World-space solid: a convex polygon (triangle or box) or a vertical circle, with its y range. */
type Solid = { material: number; lo: number; hi: number } & ({ points: number[][] } | { cx: number; cz: number; radius: number });

function solids(tiles: Tile[]): Solid[] {
  const out: Solid[] = [];
  for (const tile of tiles) {
    const [ox, oy, oz] = tile.header.origin;
    for (let t = 0; t < tile.materials.length; t++) {
      if (!OBSTACLE.has(tile.materials[t])) continue;
      const points = [0, 1, 2].map(k => {
        const i = tile.indices[t * 3 + k] * 3;
        return [ox + tile.positions[i], oy + tile.positions[i + 1], oz + tile.positions[i + 2]];
      });
      out.push({ material: tile.materials[t], points, lo: Math.min(...points.map(p => p[1])), hi: Math.max(...points.map(p => p[1])) });
    }
    for (const p of tile.primitives) {
      if (!OBSTACLE.has(p.material)) continue;
      const [cx, cy, cz] = [ox + p.center[0], oy + p.center[1], oz + p.center[2]];
      // Shipped primitives rotate about +Y only (assembler: quaternion from layer yaw).
      const yaw = 2 * Math.atan2(p.rotation[1], p.rotation[3]);
      if (p.kind === PRIM_KIND.BOX) {
        const [hx, hy, hz] = p.params, s = Math.sin(yaw), c = Math.cos(yaw);
        const points = [[-hx, -hz], [hx, -hz], [hx, hz], [-hx, hz]].map(([a, b]) => [cx + c * a + s * b, cy, cz - s * a + c * b]);
        out.push({ material: p.material, points, lo: cy - hy, hi: cy + hy });
      } else {
        const half = p.params[0] + (p.kind === PRIM_KIND.CAPSULE ? p.params[1] : 0);
        out.push({ material: p.material, cx, cz, radius: p.params[1], lo: cy - half, hi: cy + half });
      }
    }
  }
  return out;
}

/** Clip a convex polygon to lo <= y <= hi (polygons spanning the slab keep their XZ outline). */
function clip(points: number[][], lo: number, hi: number): number[][] {
  for (const [sign, level] of [[1, lo], [-1, hi]]) {
    const out: number[][] = [];
    for (let k = 0; k < points.length; k++) {
      const previous = points[(k + points.length - 1) % points.length], current = points[k];
      const a = sign * (previous[1] - level), b = sign * (current[1] - level);
      if ((a >= 0) !== (b >= 0)) out.push(previous.map((v, i) => v + (current[i] - v) * (a / (a - b))));
      if (b >= 0) out.push(current);
    }
    points = out;
    if (!points.length) break;
  }
  return points;
}

function convexHits(uv: number[][], halfWidth: number, halfLength: number): boolean {
  const axes = [[1, 0], [0, 1]];
  for (let k = 0; k < uv.length; k++) {
    const p = uv[(k + uv.length - 1) % uv.length], q = uv[k];
    if ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 > 1e-18) axes.push([p[1] - q[1], q[0] - p[0]]);
  }
  return axes.every(([au, av]) => {
    const projection = uv.map(([u, v]) => u * au + v * av);
    const radius = halfWidth * Math.abs(au) + halfLength * Math.abs(av);
    return !(Math.min(...projection) > radius || Math.max(...projection) < -radius);
  });
}

/** First solid inside the car-frame rectangle |lateral| <= halfWidth, near <= axial <= far. */
function hit(all: Solid[], car: ParkedCar, halfWidth: number, near: number, far: number): Solid | undefined {
  const s = Math.sin(car.yaw), c = Math.cos(car.yaw);
  const centre = (near + far) / 2, halfLength = (far - near) / 2;
  const cx = car.x + s * centre, cz = car.z + c * centre;
  const lo = car.y + ENVELOPE.step_m, hi = car.y + car.height, reach = Math.hypot(halfWidth, halfLength);
  const local = (x: number, z: number) => [c * (x - cx) - s * (z - cz), s * (x - cx) + c * (z - cz)];
  return all.find(solid => {
    if (solid.lo > hi || solid.hi < lo) return false;
    if ('radius' in solid) {
      const [u, v] = local(solid.cx, solid.cz);
      return Math.max(Math.abs(u) - halfWidth, 0) ** 2 + Math.max(Math.abs(v) - halfLength, 0) ** 2 <= solid.radius ** 2;
    }
    if (solid.points.every(p => p[0] < cx - reach) || solid.points.every(p => p[0] > cx + reach) ||
      solid.points.every(p => p[2] < cz - reach) || solid.points.every(p => p[2] > cz + reach)) return false;
    const points = clip(solid.points, lo, hi);
    return points.length > 0 && convexHits(points.map(p => local(p[0], p[2])), halfWidth, halfLength);
  });
}

function boxedIn(all: Solid[], car: ParkedCar) {
  const halfWidth = car.width / 2 + ENVELOPE.side_m, end = car.length / 2, axial = ENVELOPE.axial_m;
  const body = hit(all, car, halfWidth, -end, end);
  if (body) return { where: 'side', solid: body };
  const front = hit(all, car, halfWidth, end, end + axial);
  const rear = front && hit(all, car, halfWidth, -end - axial, -end);
  return front && rear ? { where: 'front-and-rear', solid: front } : undefined;
}

/** Fail every owned parked car that the shipped solids box in. */
export function checkParked(tiles: Tile[], cars: ParkedCar[], owned: (x: number, z: number) => boolean) {
  const mine = cars.filter(car => owned(car.x, car.z));
  if (!mine.length) return { status: 'skipped', reason: 'no parked cars in owned tiles' };
  const all = solids(tiles);
  const boxed = mine.flatMap(car => {
    const found = boxedIn(all, car);
    if (!found) return [];
    const at = 'radius' in found.solid ? [found.solid.cx, found.solid.cz] : [found.solid.points[0][0], found.solid.points[0][2]];
    return [{ object_id: car.object_id, x: r3(car.x), z: r3(car.z), where: found.where, material: MATERIALS[found.solid.material].name, at: at.map(r3) }];
  });
  return { status: boxed.length ? 'fail' : 'pass', cars: mine.length, envelope: ENVELOPE, boxedIn: boxed };
}

/** The car check includes boxed-in parked cars, so a failing envelope fails `cars`. */
export function withParked(cars: any, parked: ReturnType<typeof checkParked>) {
  const status = cars.status === 'fail' || parked.status === 'fail' ? 'fail' : cars.status === 'pass' || parked.status === 'pass' ? 'pass' : 'skipped';
  return { ...cars, status, parked };
}
