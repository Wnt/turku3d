import { MAT, MAT_FLAG, materialHasFlag } from '../shared/col/materials.ts';

export const isCurbMaterial = (material: number) =>
  materialHasFlag(material, MAT_FLAG.CURB) || material === MAT.curb || material === MAT.quay;

/** Per wheel response. Units: m/s, metres, kg, N·s. Four strikes cost 6–19% speed. */
export function curbResponse(speed: number, height: number, mass: number) {
  const v = Math.max(0, speed), h = Math.max(0, height);
  const blocked = h > 0.25 && v < 3;
  const loss = h < 0.03 ? 0 : Math.min(0.05, Math.max(0.016, h * 0.28));
  return {
    blocked,
    loss: blocked ? 1 : loss,
    liftImpulse: blocked ? 0 : mass * Math.min(v * h, 0.4) * 1.7,
    rumble: Math.min(1, v * h * 0.8),
  };
}
