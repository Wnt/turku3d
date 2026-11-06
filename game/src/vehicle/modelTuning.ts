import { CAR, type VehicleTuning } from '../physics/constants';
import { AUTHORED_BY_ID } from '../actors/fleet/registry';
import { fleetDamageBox } from '../actors/fleet/catalog';
import { TARMAC } from './tyres';
import { comHeight, cornerLoads } from './ride';
import type { DamageBox } from './damage';

/** Driving tuning of a real fleet model from its registry spec (actors/fleet/models/<id>.json): mass, the model's
 *  own hub layout and tyre radius, drivetrain, power, low-speed force from torque, top speed and the steering lock
 *  from the turning circle, on slip-curve tyres with its own ride (`slipCurveHandling`). Main thread only: the physics
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
  const base: VehicleTuning = {
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
  return { ...base, ...slipCurveHandling(base, entry.fleet.height, drive) };
}

/** Slip-curve handling of every fleet car (vehicle/tyres.ts, ride.ts), calibrated on the starter against Wreckfest 2's
 *  Bravion and scaled by each car's own mass and size: the same tyres (their load sensitivity is relative to the
 *  car's own corner load), steering, keyboard slew, assists and brake build-up; the centre of mass at the starter's
 *  0.375 of the body height, capped so the track keeps a static stability factor of 1.3 (no rollover on the tyres'
 *  grip); softer ride for heavier and more roll for taller bodies; a limited-slip split by drivetrain; brakes and
 *  suspension force limits that grow with the weight. */
export function slipCurveHandling(base: VehicleTuning, height: number, drive: string): Partial<VehicleTuning> {
  const m = base.mass, loads = cornerLoads(base, m);
  const track = Math.abs(base.wheels[1].z - base.wheels[0].z);
  const com = Math.min(0.375 * height, track / 2.6);
  // 1.75 / 1.5 Hz (Wreckfest 2) at the starter's weight, softer for heavier cars (vans 1.6, buses 1.25 Hz).
  const ride = 1.75 * Math.min(1.05, Math.max(0.7, (1351 / m) ** 0.15));
  return {
    // The tyre's slip, not a shrinking lock, limits cornering, so the lock barely falls with speed (about 25° at
    // 60 km/h, as Wreckfest 2's keyboard reaches).
    tyre: TARMAC, steerHalfSpeed: 40, angularDamping: 0,
    // An open differential spins the unloaded inner wheel and loses its side grip; Wreckfest's Bravion has a 35 % lock.
    differential: drive === 'FWD' ? 0.7 : drive === 'RWD' ? 0.35 : 0.5,
    // Wreckfest 2's keyboard rate: 1.5 of full input per second, about 46°/s at the road wheel at rest.
    keySteer: { rate: 1.55, centre: 3.1 },
    // Wreckfest 2's Bravion: damping 0.45 / 0.41 bump and 0.59 / 0.5 rebound, roll 3.6°/g at the starter's 1.39 m
    // height, with the game's 64 % front roll stiffness (its measured 52 % of the transfer includes roll-centre
    // geometry this model has not: 52 % here loosened the tail at the limit).
    ride: { frequency: [ride, ride * 1.5 / 1.75], bump: [0.45, 0.41], rebound: [0.59, 0.5],
      roll: 3.6 * Math.min(1.6, Math.max(1, height / 1.385)), rollFront: 0.64 },
    comDrop: Math.max(0, comHeight({ ...base, comDrop: 0 }, m) - com),
    // A light stability control, as Wreckfest's default "half": it acts only once the tail slides 2° more than the nose.
    stability: { onset: 2, full: 8, max: 0.6 },
    // Wreckfest 2: deceleration reaches 90 % in 0.16-0.19 s (the pressure ramps over 0.26 s; anti-lock caps the force at
    // about two thirds of it) and releases as fast; 60 % front balance. Brake force and spring force limits scale with
    // the weight, so a van or a bus also brakes at its tyres' limit and does not sit on its bump stops.
    brakes: { ramp: 0.26, front: 0.6 },
    brakeForce: CAR.brakeForce * Math.max(1, m / 1351),
    maxSuspensionForce: Math.max(CAR.maxSuspensionForce, 5 * Math.max(...loads)),
  };
}

/** The damage cage box of a real fleet model (vehicle/damage.ts), for its spawn spec; the renderer skins its body to
 *  the same cage (actors/fleet/damageRig.ts). */
export function modelDamageBox(id: string): DamageBox | undefined {
  const entry = AUTHORED_BY_ID.get(id);
  return entry ? fleetDamageBox(entry.fleet) : undefined;
}
