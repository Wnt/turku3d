/** Ackermann steering for +X-forward chassis coordinates (+Z is the right side).
 * Both front tyre axes meet the rear axle at the same instantaneous turn centre. */
export function ackermann(steer: number, wheelbase: number, lateral: number): number {
  if (Math.abs(steer) < 1e-6 || wheelbase <= 0) return 0;
  const tangent = Math.tan(steer);
  return Math.atan(wheelbase * tangent / Math.max(wheelbase * .25, wheelbase + lateral * tangent));
}

/** Signed wheel angle; a full circumference advances exactly one revolution. */
export function wheelRotation(distance: number, radius: number): number {
  return distance / Math.max(.01, radius);
}
