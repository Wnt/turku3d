/** Which tile props become breakable physics bodies (signs and bus stops), their ids and heights. Shared by the
 *  client (world/poleExtraction.ts) and the drive sweep (qa/drivetest/props.ts), so the sweep tests the same
 *  colliders players hit. No three.js here: the sweep reads the glTF JSON directly. */
export const pole = (e: Record<string, unknown>) => e.prop_type === 'sign' || e.prop_type === 'bus_stop';
export const poleId = (e: Record<string, unknown>) => String(e.pole_id ?? (e.tile && e.object_id ? `${e.tile}:${e.object_id}` : ''));
export const poleHeight = (e: Record<string, unknown>) => Number(e.pole_height) || 2.7;
/** A record's generator pole base, when it carries one. */
export function recordedBase(e: Record<string, unknown>): [number, number, number] | null {
  const b = e.pole_base;
  return Array.isArray(b) && b.length === 3 && b.every(v => typeof v === 'number' && Number.isFinite(v)) ? b as [number, number, number] : null;
}
