import type { VehicleState } from '../physics/protocol';
import type { V3 } from '../core/origin';

/** Nearby recovery uses the same reach as entering a vehicle, and never selects someone else's car. */
export function recoveryVehicle(vehicles: readonly VehicleState[], drivenId: string | null, feet: V3,
  mayDrive: (id: string) => boolean): VehicleState | null {
  if (drivenId) return vehicles.find(v => v.id === drivenId) ?? null;
  return vehicles.filter(v => v.id && mayDrive(v.id) && Math.abs(v.pos.y - feet.y) < 3
    && Math.hypot(v.pos.x - feet.x, v.pos.z - feet.z) < 4)
    .sort((a, b) => Math.hypot(a.pos.x - feet.x, a.pos.z - feet.z) - Math.hypot(b.pos.x - feet.x, b.pos.z - feet.z))[0] ?? null;
}

/** One continuous second on the side/roof and still; changing cars or walking away starts afresh. */
export class FlippedCarMonitor {
  private id: string | null = null;
  private seconds = 0;
  private rot: VehicleState['rot'] | null = null;
  update(dt: number, vehicle: VehicleState | null): boolean {
    const same = !!vehicle?.id && vehicle.id === this.id;
    const q = vehicle?.rot;
    const up = q ? 1 - 2 * (q.x * q.x + q.z * q.z) : 1;
    const rotating = same && q && this.rot
      && 2 * Math.acos(Math.min(1, Math.abs(q.x * this.rot.x + q.y * this.rot.y + q.z * this.rot.z + q.w * this.rot.w))) > dt * 0.3;
    const still = vehicle && Math.hypot(vehicle.linvel.x, vehicle.linvel.y, vehicle.linvel.z) < 0.5 && !rotating;
    this.seconds = up < 0.3 && still ? (same ? this.seconds : 0) + dt : 0;
    this.id = vehicle?.id ?? null;
    this.rot = q ? { ...q } : null;
    return this.seconds >= 1;
  }
}
