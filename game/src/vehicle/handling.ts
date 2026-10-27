import { CAR } from '../physics/constants.ts';
import type { VehicleInput } from '../physics/protocol.ts';

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** GTA-style LT brakes forward motion and reverses once stopped. */
export function handling(input: VehicleInput | null, forwardSpeed: number) {
  const speed = Math.abs(forwardSpeed);
  const throttle = clamp(input?.throttle ?? 0, -1, 1);
  const pedal = clamp(input?.brake ?? 0, 0, 1);
  let drive = throttle;
  let brake = input ? 0 : CAR.brakeForce * 0.5;
  if (pedal > 0) {
    if (forwardSpeed > 0.4 || throttle > 0) brake = pedal * CAR.brakeForce;
    else drive = -pedal;
  }
  if (drive * forwardSpeed < -0.4) { brake = Math.abs(drive) * CAR.brakeForce; drive = 0; }
  const limit = drive < 0 ? 8 : CAR.topSpeed;
  const engine = drive * CAR.engineForce * clamp((limit - speed) / 5, 0, 1);
  return {
    engine, brake,
    steer: -clamp(input?.steer ?? 0, -1, 1) * CAR.maxSteer / (1 + (speed / 10) ** 2),
    // Progressive tyre slip, with a rear-only handbrake drift.
    frontGrip: 2.4 - Math.min(0.5, speed / 60),
    rearGrip: input?.handbrake ? 0.65 : 2.2 - Math.min(0.4, speed / 65),
    handbrake: input?.handbrake ?? false,
  };
}
