import type { V3 } from '../core/origin';

/** Additional roll impulse from the tyre force acting below the centre of mass.
 * Rapier fixes wheel roll_influence at 0.1, without a JS setter. Restore a bounded
 * part of that missing lever arm; the real springs then carry the weight transfer.
 * https://github.com/dimforge/rapier/blob/master/src/control/ray_cast_vehicle_controller.rs
 * No force is added: trajectory and tyre grip remain the controller's responsibility. */
export function tyreRollImpulse(mass: number, lateralDeltaV: number, height: number,
  dt: number, coupling: number, contacts: number, upY: number): number {
  if (contacts < 2 || upY < 0.5 || dt <= 0) return 0;
  const limit = 12 * dt / contacts;
  const delta = Math.max(-limit, Math.min(limit, lateralDeltaV));
  return -mass * delta * Math.max(0, Math.min(1.2, height)) * coupling;
}

export const dot = (a: V3, b: V3) => a.x * b.x + a.y * b.y + a.z * b.z;
