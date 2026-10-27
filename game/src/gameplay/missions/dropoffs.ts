// Street-level check of a drop-off before its marker is placed: the ground under the point must not be a building
// (roof or wall) and must lie within MAX_STREET_STEP_M of the street around it. A rejected drop-off is moved by the
// mission system (`MissionSystem.relocateDropoff`) to its next candidate, so a marker never sits on a roof.
import type { RouteTarget, WorldPoint } from '../contract';
import { MAT, MATERIALS } from '../../shared/col/materials';

/** First solid hit of a downward ray at (x, z): its height and collision material, or null while no collider. */
export type GroundRay = (x: number, z: number) => Promise<{ y: number; material: number } | null>;

/** Largest height difference (m) between a drop-off and the street around it. */
export const MAX_STREET_STEP_M = 2;
/** The street around a drop-off: rays on two rings of eight around it. */
const RING_RADII_M = [5, 9];
const RING_RAYS = 8;
/** Collision materials of buildings: a drop-off on one is inside a footprint. */
const FOOTPRINT: ReadonlySet<number> = new Set([MAT.building, MAT.roof, MAT.wall]);

type DropoffCheck = { ok: true; y: number } | { ok: false; reason: string };

/** Checks a drop-off against the collision around it; null while its collision is not resident. */
export async function checkDropoff(point: WorldPoint, ray: GroundRay): Promise<DropoffCheck | null> {
  const at = await ray(point.x, point.z);
  if (!at) return null;
  const y = at.y.toFixed(1);
  if (FOOTPRINT.has(at.material)) {
    return { ok: false, reason: `inside a building footprint (${MATERIALS[at.material].name} at y ${y})` };
  }
  const ring = await Promise.all(RING_RADII_M.flatMap(r => Array.from({ length: RING_RAYS }, (_, i) => {
    const a = (2 * Math.PI * i) / RING_RAYS;
    return ray(point.x + r * Math.cos(a), point.z + r * Math.sin(a));
  })));
  if (ring.every(h => h === null)) return null;
  const street = ring.filter(h => h && !FOOTPRINT.has(h.material)).map(h => h!.y).sort((a, b) => a - b);
  if (street.length === 0) return { ok: false, reason: `no street around it (ground y ${y})` };
  const level = street[Math.floor(street.length / 2)];
  if (Math.abs(at.y - level) > MAX_STREET_STEP_M) {
    return { ok: false, reason: `ground y ${y} is ${Math.abs(at.y - level).toFixed(1)} m from the street (y ${level.toFixed(1)})` };
  }
  return { ok: true, y: at.y };
}

/** Where a rejected drop-off goes next (`MissionSystem.relocateDropoff`): a new point, or null when none is left. */
type Relocate = (id: string, point: WorldPoint) => WorldPoint | null;

/** Ground height of the drop-off markers (`ZoneMarkers`): checks each drop-off before its marker is placed. */
export class DropoffGuard {
  /** Points every candidate of which was rejected: they get no marker, and warn once. */
  private readonly abandoned = new Set<string>();

  constructor(private readonly ray: GroundRay, private readonly relocate: Relocate) {}

  /** Marker height for a drop-off target, or null while its collision is not resident or once it was rejected
   *  (the mission system then lists the target at its new point, which is checked in turn). */
  async ground(target: RouteTarget): Promise<number | null> {
    const key = `${target.id}@${target.point.x},${target.point.z}`;
    if (this.abandoned.has(key)) return null;
    const check = await checkDropoff(target.point, this.ray);
    if (!check) return null;
    if (check.ok) return check.y;
    const { x, z } = target.point;
    const next = this.relocate(target.id, target.point);
    console.warn(`[missions] drop-off ${target.label} at ${x}, ${z} rejected: ${check.reason}; `
      + (next ? `moved to ${next.x.toFixed(2)}, ${next.z.toFixed(2)}` : 'no street-level candidate left, no marker'));
    if (!next) this.abandoned.add(key);
    return null;
  }
}
