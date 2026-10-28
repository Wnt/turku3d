import type { Router, RouteTarget, WorldPoint } from './contract';
import type { TravelMode } from './roadGraph';

export const REROUTE_M = 5;
export const OFF_ROUTE_M = 25;

/** Keep the planned corridor while progressing; replan only after sustained deviation. */
export class RouteTracker {
  route: WorldPoint[] | null = null;
  length = 0;
  computeMs = 0;
  private origin: WorldPoint | null = null;
  private key = '';
  private offSince: number | null = null;
  private plannedAt = -Infinity;
  constructor(private readonly router: Router) {}

  /** New streamed roads only invalidate a failed plan, never a valid corridor. */
  invalidate() { if (!this.route) this.origin = null; }

  update(position: WorldPoint, target: RouteTarget | null, mode: TravelMode): boolean {
    const key = target ? `${target.id}|${target.point.x}|${target.point.z}|${mode}` : '';
    const changed = key !== this.key;
    const now = performance.now();
    const moved = this.origin ? Math.hypot(position.x - this.origin.x, position.z - this.origin.z) : Infinity;
    if (!changed && (!target || moved <= REROUTE_M)) {
      this.offSince = null;
      return false;
    }
    if (!changed && this.route && this.route.length > 1) {
      const nearest = projectRoute(position, this.route);
      if (nearest.distance <= OFF_ROUTE_M) {
        this.offSince = null;
        this.origin = { ...position };
        this.route = [nearest.point, ...this.route.slice(nearest.segment + 1)];
        this.length = this.router.distance(this.route);
        return true;
      }
      this.offSince ??= now;
      if (now - this.offSince < 1000 || now - this.plannedAt < 2000) return false;
    }
    this.key = key;
    this.origin = { ...position };
    this.offSince = null;
    this.plannedAt = now;
    const t0 = performance.now();
    this.route = target ? this.router.route(position, target.point) : null;
    this.computeMs = performance.now() - t0;
    this.length = this.route ? this.router.distance(this.route) : 0;
    return true;
  }
}

function projectRoute(position: WorldPoint, route: WorldPoint[]) {
  let distance = Infinity, segment = 0, point = route[0];
  for (let i = 0; i + 1 < route.length; i++) {
    const a = route[i], b = route[i + 1], dx = b.x - a.x, dz = b.z - a.z;
    const t = Math.max(0, Math.min(1, ((position.x - a.x) * dx + (position.z - a.z) * dz) / (dx * dx + dz * dz || 1)));
    const p = { x: a.x + t * dx, z: a.z + t * dz };
    const d = Math.hypot(position.x - p.x, position.z - p.z);
    if (d < distance) { distance = d; segment = i; point = p; }
  }
  return { distance, segment, point };
}

/** Expanded north-up map includes the player and every remaining route vertex. */
export function routeView(position: WorldPoint, route: WorldPoint[] | null, minimum: number) {
  let xmin = position.x, xmax = xmin, zmin = position.z, zmax = zmin;
  for (const p of route ?? []) {
    xmin = Math.min(xmin, p.x); xmax = Math.max(xmax, p.x);
    zmin = Math.min(zmin, p.z); zmax = Math.max(zmax, p.z);
  }
  return { centre: { x: (xmin + xmax) / 2, z: (zmin + zmax) / 2 }, span: Math.max(minimum, (xmax - xmin) * 1.2, (zmax - zmin) * 1.2) };
}
