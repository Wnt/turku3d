import { SCOOTER } from '../physics/constants.ts';

/** Cosmetic two-wheeler lean limit (radians). */
export const MAX_LEAN = 15 * Math.PI / 180;
const WHEELBASE = SCOOTER.wheels[0].x - SCOOTER.wheels[1].x;

/** Lean (radians, positive into a left turn = positive steer) that balances the lateral acceleration of a
 *  kinematic bicycle at this forward speed and front-wheel steer angle. */
export function scooterLean(forwardSpeed: number, steer: number): number {
  const lateral = forwardSpeed * forwardSpeed * Math.tan(steer) / WHEELBASE;
  return Math.max(-MAX_LEAN, Math.min(MAX_LEAN, Math.atan(lateral / 9.81)));
}
