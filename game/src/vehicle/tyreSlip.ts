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

function validContact(wheel: WheelState): boolean {
  const t = wheel.tyre;
  if (!wheel.contact || !t || !acceptsTyreMarks(t.material) || !t.point || !t.normal
    || !Number.isFinite(t.lateral) || !Number.isFinite(t.longitudinal)
    || !Number.isFinite(t.point.x) || !Number.isFinite(t.point.y) || !Number.isFinite(t.point.z)
    || !Number.isFinite(t.normal.x) || !Number.isFinite(t.normal.y) || !Number.isFinite(t.normal.z)
    || Math.hypot(t.point.x, t.point.y, t.point.z) > 10 || Math.abs(Math.hypot(t.normal.x, t.normal.y, t.normal.z) - 1) > 0.05) return false;
  return Number.isFinite(wheel.suspension);
}

/** Speed-weighted sliding demand; effect consumers must also apply TyreSlipFilter. */
export function tyreStrength(wheel: WheelState, speed = 0): number {
  if (!validContact(wheel) || !Number.isFinite(speed)) return 0;
  const t = wheel.tyre!, v = Math.min(30, Math.abs(speed));
  return Math.min(1, Math.max(0, (Math.hypot(t.lateral / (2.2 + v * 0.05), t.longitudinal / (2.8 + v * 0.06)) - 1) * 0.8));
}

/** Client-only contact settling and sustained-slide hysteresis, shared by local/remote visuals and audio. */
export class TyreSlipFilter {
  private readonly contact: Float64Array;
  private readonly pending: Float64Array;
  private readonly release: Float64Array;
  private readonly strength: Float64Array;
  private readonly suspension: Float64Array;

  constructor(count: number) {
    this.contact = new Float64Array(count); this.pending = new Float64Array(count);
    this.release = new Float64Array(count); this.strength = new Float64Array(count);
    this.suspension = new Float64Array(count).fill(NaN);
  }

  reset(id: number) {
    this.contact[id] = this.pending[id] = this.release[id] = this.strength[id] = 0;
    this.suspension[id] = NaN;
  }

  update(id: number, wheel: WheelState, speed: number, dt: number): number {
    if (!validContact(wheel) || !Number.isFinite(speed) || dt > 0.1) { this.reset(id); return 0; }
    if (!(dt > 0)) return this.strength[id];
    const previous = this.suspension[id];
    this.suspension[id] = wheel.suspension;
    if (!Number.isFinite(previous) || Math.abs(wheel.suspension - previous) / dt > 1.5) {
      this.contact[id] = this.pending[id] = this.release[id] = this.strength[id] = 0;
      return 0;
    }
    this.contact[id] += dt;
    if (this.contact[id] < 0.08) return 0;
    const demand = tyreStrength(wheel, speed);
    if (!this.strength[id]) {
      this.pending[id] = demand > 0 ? this.pending[id] + dt : 0;
      if (this.pending[id] >= 0.18) this.strength[id] = demand;
    } else if (demand > 0) {
      this.release[id] = 0; this.strength[id] = demand;
    } else {
      this.release[id] += dt;
      if (this.release[id] >= 0.1) this.reset(id);
    }
    return this.strength[id];
  }
}
