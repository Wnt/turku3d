import type { ParkedVehicle } from '../world/manifest';
import type { NetPose } from './client';

/** The key in the shipped vehicle model manifest, or the built-in starter vehicle. */
export function vehicleIdentity(mode: NetPose['mode'], drivenId: string | null,
  parked: readonly ParkedVehicle[]): string | null {
  if (mode === 'foot') return null;
  if (mode === 'scooter') return 'scooter';
  if (!drivenId || drivenId === 'player') return 'player';
  const vehicle = parked.find(v => v.object_id === drivenId);
  if (!vehicle) throw new Error(`missing driven vehicle identity: ${drivenId}`);
  return `${vehicle.model}/${vehicle.color}`;
}
