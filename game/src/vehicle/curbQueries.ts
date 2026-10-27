import type * as Rapier from '@dimforge/rapier3d';
type RapierApi = Pick<typeof Rapier, 'Ball' | 'ColliderDesc' | 'TriMesh'>;
import type { V3 } from '../core/origin.ts';
import { rotate } from './math.ts';

export interface CurbHit { handle: number; point: V3; normal: V3; material: number; top: number; distance: number }
export interface WheelQueries { castWheel(center: V3, direction: V3, distance: number, radius: number): CurbHit | null }
interface Surface {
  triangles: number;
  sample(point: V3): { material: number; top: number } | null;
}
const zero = { x: 0, y: 0, z: 0 }, identity = { x: 0, y: 0, z: 0, w: 1 };

/** Material-selected submeshes, queried without changing the walkable solid mesh topology. */
export class CurbQueries implements WheelQueries {
  private surfaces = new Map<number, Surface>();
  private wheels = new Map<number, Rapier.Ball>();
  private world: Rapier.World;
  private R: RapierApi;
  constructor(R: RapierApi, world: Rapier.World) { this.R = R; this.world = world; }
  add(collider: Rapier.Collider, shape: Rapier.Shape, sample: Surface['sample']): Rapier.Collider {
    const p = collider.translation();
    // Cache the submesh BVH in Rapier. Collision groups 0 and sensor status exclude it from
    // all gameplay queries/contacts; this module alone calls its pairwise shape cast.
    const query = this.world.createCollider(new this.R.ColliderDesc(shape).setTranslation(p.x, p.y, p.z)
      .setRotation(collider.rotation()).setCollisionGroups(0).setSolverGroups(0).setSensor(true));
    this.surfaces.set(query.handle, { sample, triangles: shape instanceof this.R.TriMesh ? shape.indices.length / 3 : 0 });
    return query;
  }
  triangleCount(handle: number) { return this.surfaces.get(handle)?.triangles ?? 0; }
  remove(handle: number) { this.surfaces.delete(handle); }

  castWheel(center: V3, direction: V3, distance: number, radius: number): CurbHit | null {
    let closest: CurbHit | null = null;
    let wheel = this.wheels.get(radius);
    if (!wheel) this.wheels.set(radius, wheel = new this.R.Ball(radius));
    const mid = { x: center.x + direction.x * distance / 2, y: center.y, z: center.z + direction.z * distance / 2 };
    const r = radius + 0.01;
    this.world.collidersWithAabbIntersectingAabb(mid, { x: r + Math.abs(direction.x) * distance / 2, y: r, z: r + Math.abs(direction.z) * distance / 2 }, c => {
      const s = this.surfaces.get(c.handle);
      if (!s) return true;
      const q = c.rotation(), p = c.translation();
      const hit = c.castShape(zero, wheel, center, identity, direction, 0, distance, false);
      if (!hit || (closest && hit.time_of_impact >= closest.distance)) return true;
      // Collider.castShape returns witnesses in each shape's LOCAL frame.
      const point = rotate(hit.witness1, q); point.x += p.x; point.y += p.y; point.z += p.z;
      const surface = s.sample(point);
      if (surface) closest = { handle: c.handle, point, normal: rotate(hit.normal1, q), distance: hit.time_of_impact, ...surface };
      return true;
    });
    return closest;
  }
}
