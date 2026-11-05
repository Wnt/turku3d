/** Ackermann steering for +X-forward chassis coordinates (+Z is the right side).
 * Both front tyre axes meet the rear axle at the same instantaneous turn centre. */
export function ackermann(steer: number, wheelbase: number, lateral: number): number {
  if (Math.abs(steer) < 1e-6 || wheelbase <= 0) return 0;
  const tangent = Math.tan(steer);
  return Math.atan(wheelbase * tangent / Math.max(wheelbase * .25, wheelbase + lateral * tangent));
}

/** Four-wheel-steer Ackermann (SPEC §3.2). `front` and `rear` are the signed inner-wheel angles of each axle (+ left,
 *  counter-phase rear negative), `xf`/`xr` the axle x positions and `track` the lateral wheel spacing. Returns the
 *  angle of the wheel at chassis (x, z) about the common instant centre, clamped to `limit`. Rear 0 is the classic
 *  front-steer layout (centre on the rear axle line); equal tangents (crab) steer every wheel in parallel. */
export function ackermann4(front: number, rear: number, xf: number, xr: number, track: number,
  x: number, z: number, limit: number): number {
  const tf = Math.tan(front), tr = Math.tan(rear);
  const own = x > (xf + xr) / 2 ? front : rear;
  if (Math.abs(tf) < 1e-6 && Math.abs(tr) < 1e-6) return 0;
  if (Math.abs(tr - tf) < 1e-4) return Math.max(-limit, Math.min(limit, own));
  // An axle held straight puts the turn centre on its own line: its wheels stay exactly straight (no rounding in xc).
  if (own === 0) return 0;
  const xc = (xf * tr - xr * tf) / (tr - tf);
  // Signed distance of the inner wheel line from the centre (+: centre on the left, −Z).
  const inner = Math.abs(tf) >= Math.abs(tr) ? (xf - xc) / tf : (xr - xc) / tr;
  const zc = -Math.sign(inner) * track / 2 - inner;
  const angle = Math.atan((x - xc) / (z - zc));
  return Math.max(-limit, Math.min(limit, angle));
}

/** Signed wheel angle; a full circumference advances exactly one revolution. */
export function wheelRotation(distance: number, radius: number): number {
  return distance / Math.max(.01, radius);
}
