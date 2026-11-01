import type { ParkedVehicle } from './manifest';
// The only coupling to the day cycle. When local/night-look merges, this becomes its nightFactor of the
// shared clock and nothing else here changes.
import { nightLevel } from '../render/lights/night';

/** Occupancy of each slot class by day (night 0) and at full night (night 1), as in a GTA-style
 * time-of-day car generator: the centre empties in the evening, residential streets fill up. The
 * larger of the two must equal the generator's PEAK for that class (pipeline/turku3d/coarse/parked.py),
 * which places a car only where its draw is below that peak. */
export const OCCUPANCY: Readonly<Record<string, { day: number; night: number }>> = {
  'centre-kerb': { day: 0.9, night: 0.62 },
  'centre-lot': { day: 0.85, night: 0.35 },
  'residential-kerb': { day: 0.74, night: 0.92 },
  'residential-lot': { day: 0.62, night: 0.9 },
};
/** Inferred kerbs (priority 0) empty first. */
export const LOW_PRIORITY = 0.85;

/** Night level 0..1 of the shared clock, read from the same inputs as the street lights. */
export function parkedNight(time: Date, sunElevation: number): number {
  return nightLevel({ time, sunElevation });
}

export function occupancy(zone: string, night: number): number {
  const curve = OCCUPANCY[zone];
  if (!curve) return 1;
  const t = Math.min(1, Math.max(0, night));
  return curve.day + (curve.night - curve.day) * t;
}

/** Whether a draft slot holds its car at this night level. A pure function of the slot's own draw and
 * the shared clock, so every client shows the same cars; slots without a draw always show. */
export function slotShown(car: Pick<ParkedVehicle, 'zone' | 'occ' | 'priority'>, night: number): boolean {
  if (car.occ === undefined || car.zone === undefined) return true;
  return car.occ < occupancy(car.zone, night) * (car.priority === 0 ? LOW_PRIORITY : 1);
}
