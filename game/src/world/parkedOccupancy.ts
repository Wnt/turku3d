import type { ParkedVehicle } from './manifest';
import { cycleAt, dayCycle } from '../core/dayCycle';

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

/** Night level 0..1 at a shared-clock instant: the shared cycle's, or with `viewHour` (single player's
 * DayClock.hourAt: ?time=, ?cycle=, the developer time panel) the cycle's night at the hour this browser shows. A
 * multiplayer client passes none, so a local view never changes which cars stand in the shared world. */
export function parkedNight(sharedUtcMs: number, viewHour?: (sharedUtcMs: number) => number): number {
  return viewHour ? cycleAt(viewHour(sharedUtcMs)).night : dayCycle(sharedUtcMs).night;
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
