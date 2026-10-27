// Character and car tuning shared by the worker (physics/world.ts) and the visuals.

/** Fixed physics substep (60 Hz). `__game.clock.simSeconds` counts these steps. */
export const DT = 1 / 60;

export const CHARACTER = {
  radius: 0.3,
  halfHeight: 0.6, // capsule total height 1.8 m
  skin: 0.02,
  autostepHeight: 0.35,
  autostepMinWidth: 0.15,
  snap: 0.3,
  maxClimbDeg: 45,
  minSlideDeg: 50,
  gravity: 9.81,
  jumpSpeed: 4.6,
  groundedVy: -0.5, // m/s while grounded (keeps contact without defeating autostep; tools/validator/src/kcc.ts)
} as const;

/** Physics tuning of one vehicle class. Wheels: chassis-frame connection points, +X forward. */
export interface VehicleTuning {
  halfExt: { x: number; y: number; z: number };
  mass: number;
  /** Centre of mass below the chassis centre (m); a two-wheeler keeps it low so its outriggers never tip. */
  comDrop: number;
  wheelRadius: number;
  rest: number;
  wheels: readonly { x: number; y: number; z: number }[];
  /** Wheel indices that steer (and use front grip) and that the engine and handbrake act on. */
  steered: readonly number[];
  driven: readonly number[];
  topSpeed: number;
  reverseSpeed: number;
  engineForce: number; // N per driven wheel
  brakeForce: number;
  maxSteer: number;
  /** Speed (m/s) at which the steering lock has fallen to half. */
  steerHalfSpeed: number;
  /** Steering rate limit (rad/s). */
  steerRate: number;
  suspensionStiffness: number;
  maxSuspensionForce: number;
}

export const CAR = {
  halfExt: { x: 2.1, y: 0.3, z: 0.9 }, // forward = +X in the chassis frame
  mass: 1200,
  comDrop: 0,
  wheelRadius: 0.33,
  rest: 0.3,
  wheels: [
    { x: 1.35, y: -0.15, z: -0.8 }, { x: 1.35, y: -0.15, z: 0.8 },
    { x: -1.35, y: -0.15, z: -0.8 }, { x: -1.35, y: -0.15, z: 0.8 },
  ],
  steered: [0, 1],
  driven: [2, 3],
  topSpeed: 27,
  reverseSpeed: 8,
  engineForce: 3200,
  brakeForce: 100,
  maxSteer: 0.55,
  steerHalfSpeed: 10,
  steerRate: 2.5,
  suspensionStiffness: 38,
  maxSuspensionForce: 18000,
} as const satisfies VehicleTuning;

/** Moped/scooter: a front and a rear wheel on the centre line plus two invisible outrigger wheels near the rear
 *  keep the ray-cast controller upright; the visual lean is cosmetic (actors/scooter.ts). Rider included in mass. */
export const SCOOTER = {
  halfExt: { x: 1.02, y: 0.3, z: 0.3 },
  mass: 190,
  comDrop: 0.35,
  wheelRadius: 0.24,
  rest: 0.2,
  wheels: [
    { x: 0.785, y: -0.12, z: 0 }, { x: -0.465, y: -0.12, z: 0 },
    { x: -0.15, y: -0.12, z: -0.36 }, { x: -0.15, y: -0.12, z: 0.36 },
  ],
  steered: [0],
  driven: [1],
  topSpeed: 13.8,
  reverseSpeed: 2,
  engineForce: 600,
  brakeForce: 5,
  maxSteer: 0.7,
  steerHalfSpeed: 6,
  steerRate: 3.2,
  suspensionStiffness: 45,
  maxSuspensionForce: 6000,
} as const satisfies VehicleTuning;

export type VehicleKind = 'car' | 'scooter';
export const VEHICLES: Record<VehicleKind, VehicleTuning> = { car: CAR, scooter: SCOOTER };
/** Target top speed of the scooter (45 km/h); the engine limit sits above it because drag balances the taper. */
export const SCOOTER_TARGET_SPEED = 12.5;
