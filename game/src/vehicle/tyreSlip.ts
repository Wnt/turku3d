import type { WheelState } from '../physics/protocol';
import { MAT } from '../shared/col/materials';

/** Rapier's ray vehicle has no simulated wheel angular inertia. Estimate longitudinal sliding
 * from demanded engine/brake impulse beyond the solver's suspension-load friction budget. */
export function longitudinalSlip(speed: number, demand: number, grip: number): number {
  return Math.abs(speed) < 1 || demand <= 0 ? 0 : Math.abs(speed) * Math.max(0, 1 - Math.max(0, grip) / demand);
}

export function acceptsTyreMarks(material: number): boolean {
  return material === MAT.road || material === MAT.sidewalk || material === MAT.curb || material === MAT.curb_dropped
    || material === MAT.crossing || material === MAT.cycleway || material === MAT.cobblestone
    || material === MAT.paving_slab || material === MAT.quay || material === MAT.ramp || material === MAT.bridge_deck;
}

/** Shared by marks, smoke and the existing screech loop. Sliding speed is in m/s. */
export function tyreStrength(wheel: WheelState): number {
  const t = wheel.tyre;
  if (!wheel.contact || !t || !acceptsTyreMarks(t.material) || !t.point || !t.normal
    || !Number.isFinite(t.lateral) || !Number.isFinite(t.longitudinal)
    || !Number.isFinite(t.point.x) || !Number.isFinite(t.point.y) || !Number.isFinite(t.point.z)
    || !Number.isFinite(t.normal.x) || !Number.isFinite(t.normal.y) || !Number.isFinite(t.normal.z)
    || Math.hypot(t.point.x, t.point.y, t.point.z) > 10 || Math.abs(Math.hypot(t.normal.x, t.normal.y, t.normal.z) - 1) > 0.05) return 0;
  return Math.min(1, Math.max(0, (Math.hypot(t.lateral, t.longitudinal) - 1.5) / 5));
}
