import { CYCLE_EPOCH, DAY_MS } from '../core/dayCycle';

/** Rare race-team appearances: each generator-selected bay is occupied for one of four shared days.
 * The source slot is already a 1/1000 draw. Every peer computes the same phase from its stable id;
 * camera time, graphics settings, tile loading order and local frame rate never affect the result.
 * Claimed or rolling vehicles are retained by ParkedVehicles across the end of their window.
 */
export function prerunnerPresent(id: string, sharedUtcMs: number): boolean {
  let hash = 2166136261;
  for (let i = 0; i < id.length; i++) hash = Math.imul(hash ^ id.charCodeAt(i), 16777619);
  const day = Math.floor((sharedUtcMs - CYCLE_EPOCH) / DAY_MS);
  return ((day % 4) + 4) % 4 === (hash >>> 0) % 4;
}
