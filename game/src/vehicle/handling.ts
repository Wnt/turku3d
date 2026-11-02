import { CAR, type VehicleTuning } from '../physics/constants.ts';
import type { VehicleInput } from '../physics/protocol.ts';

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** GTA-style LT brakes forward motion and reverses once stopped. */
export function handling(input: VehicleInput | null, forwardSpeed: number, tuning: VehicleTuning = CAR) {
  const speed = Math.abs(forwardSpeed);
  const throttle = clamp(input?.throttle ?? 0, -1, 1);
  const pedal = clamp(input?.brake ?? 0, 0, 1);
  let drive = throttle;
  let brake = input ? 0 : tuning.brakeForce * 0.5;
  if (pedal > 0) {
    if (forwardSpeed > 0.4 || throttle > 0) brake = pedal * tuning.brakeForce;
    else drive = -pedal;
  }
  if (drive * forwardSpeed < -0.4) { brake = Math.abs(drive) * tuning.brakeForce; drive = 0; }
  const limit = drive < 0 ? tuning.reverseSpeed : tuning.topSpeed;
  // Low gear is force limited; higher road speed becomes power limited. Applying
  // power across all driven wheels avoids multiplying output by the axle count.
  const wheelForce = tuning.engineForce * clamp((limit - speed) / 5, 0, 1);
  const powerForce = tuning.enginePower / (Math.max(1, speed) * tuning.driven.length);
  const engine = drive * Math.min(wheelForce, powerForce);
  return {
    engine, brake,
    steer: -clamp(input?.steer ?? 0, -1, 1) * tuning.maxSteer / (1 + (speed / tuning.steerHalfSpeed) ** 2),
    // Progressive tyre slip, with a rear-only handbrake drift.
    frontGrip: 2.4 - Math.min(0.5, speed / 60),
    rearGrip: input?.handbrake ? 0.65 : 2.2 - Math.min(0.4, speed / 65),
    handbrake: input?.handbrake ?? false,
  };
}
