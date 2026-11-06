import { CAR, type VehicleTuning } from '../physics/constants';
import { AUTHORED_BY_ID } from '../actors/fleet/registry';
import { fleetDamageBox } from '../actors/fleet/catalog';
import type { DamageBox } from './damage';

/** Driving tuning of a real fleet model from its registry spec (actors/fleet/models/<id>.json): mass, the model's
 *  own hub layout and tyre radius, drivetrain, power, low-speed force from torque, top speed and the steering lock
 *  from the turning circle. Suspension and friction stay the shared car baseline. Main thread only: the physics
 *  worker receives the result in its spawn spec. Model space is +Z forward, the chassis frame +X forward. */
export function modelTuning(id: string): VehicleTuning | undefined {
  const entry = AUTHORED_BY_ID.get(id);
  if (!entry) return undefined;
  const spec = entry.spec as { massKg?: number; powerKW?: number; torqueNm?: number; drivetrain?: string; topSpeedKmh?: number; turningCircleM?: number };
  const p = entry.wheels.pivots, r = entry.wheels.radius;
  // CAR attaches wheels at y -0.15 for its 0.33 m tyre; keep the same road height for this tyre radius.
  const y = CAR.wheels[0].y + (r - CAR.wheelRadius);
  const wheel = (name: keyof typeof p) => ({ x: p[name][2], y, z: -p[name][0] });
  // Physics order (constants CAR): front pair, then rear pair; within a pair chassis z -, then +.
  const wheels = [wheel('wheel_FR'), wheel('wheel_FL'), wheel('wheel_RR'), wheel('wheel_RL')]
    .sort((a, b) => b.x - a.x || a.z - b.z);
  const wheelbase = wheels[0].x - wheels[2].x, track = Math.abs(wheels[0].z - wheels[1].z);
  const drive = spec.drivetrain ?? 'FWD';
  const turn = spec.turningCircleM ?? 11;
  return {
    ...CAR,
    halfExt: { x: entry.fleet.length * .47, y: CAR.halfExt.y, z: Math.min(entry.fleet.width / 2, track / 2 + .15) },
    mass: spec.massKg ?? CAR.mass,
    wheelRadius: r,
    wheels,
    driven: drive === 'FWD' ? [0, 1] : drive === 'RWD' ? [2, 3] : [0, 1, 2, 3],
    frontTorque: drive === 'FWD' ? 1 : drive === 'RWD' ? 0 : .45,
    enginePower: (spec.powerKW ?? CAR.enginePower / 1000) * 1000 * .86,
    engineForce: CAR.engineForce * (spec.torqueNm ?? 250) / 250,
    topSpeed: Math.min((spec.topSpeedKmh ?? 200) / 3.6, 45),
    maxSteer: Math.max(.4, Math.min(.65, Math.atan(wheelbase / Math.max(1, turn / 2 - track / 2)))),
  };
}

/** The damage cage box of a real fleet model (vehicle/damage.ts), for its spawn spec; the renderer skins its body to
 *  the same cage (actors/fleet/damageRig.ts). */
export function modelDamageBox(id: string): DamageBox | undefined {
  const entry = AUTHORED_BY_ID.get(id);
  return entry ? fleetDamageBox(entry.fleet) : undefined;
}
