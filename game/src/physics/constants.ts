// Character and car tuning shared by the worker (physics/world.ts) and the visuals.
import type { RearSteerTuning } from '../vehicle/rearSteer.ts';
import type { SuspensionCurve } from '../vehicle/suspension.ts';

/** Fixed physics substep (60 Hz). `__game.clock.simSeconds` counts these steps. */
export const DT = 1 / 60;
/** Collision membership bit of remote players' car proxies (physics/world.ts setRemotes), beside the generated GROUP
 *  bits (shared/col/materials.ts): the local chassis and the character collide with it; wheel rays never see it. */
export const REMOTE_VEHICLE = 128;
/** Hard bound for both wall-clock catch-up and the fixed-step accumulator. */
export const MAX_SUBSTEPS = 5;

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
  /** Wheel indices on the front axle: front grip, the front torque share, and the wheels the handbrake leaves free. */
  front: readonly number[];
  /** Front wheel indices the steering input turns (rear steer, when present, is `rearSteer`). */
  steered: readonly number[];
  driven: readonly number[];
  topSpeed: number;
  reverseSpeed: number;
  engineForce: number; // N per driven wheel before axle torque distribution
  /** Maximum mechanical power delivered to all driven tyres (W). */
  enginePower: number;
  brakeForce: number;
  maxSteer: number;
  /** Speed (m/s) at which the steering lock has fallen to half. */
  steerHalfSpeed: number;
  /** Steering rate limit (rad/s). */
  steerRate: number;
  suspensionStiffness: number;
  /** Damping coefficients (per unit chassis mass) while the spring compresses and while it extends. */
  suspensionCompression: number;
  suspensionRelaxation: number;
  /** Suspension travel either side of the rest length (m). */
  suspensionTravel: number;
  maxSuspensionForce: number;
  /** Additional tyre-force roll lever (Rapier internally retains only 10%). */
  rollTransfer: number;
  /** Share of the engine force on driven steered (front) wheels; the rest goes to the driven rear wheels. */
  frontTorque: number;
  /** Scale of the curb strike's speed loss and lift impulse: long-travel suspension soaks a kerb up. */
  curbFeel: number;
  /** Centre of mass ahead of the chassis centre (m); sets the front/rear weight split. Absent: 0. */
  comAhead?: number;
  /** Four-wheel steering (vehicle/rearSteer.ts). `maxSteer` is then the front inner-wheel lock and the wheels follow
   *  `ackermann4`. Absent: front steer only with the centre-line `ackermann`. */
  rearSteer?: RearSteerTuning;
  /** Long-travel suspension scheduled per tick (vehicle/suspension.ts). Absent: Rapier's linear spring and damper. */
  suspensionCurve?: SuspensionCurve;
  /** Base friction slip on asphalt and its multipliers per surface class (MATERIALS[].audio). Absent: the car grip. */
  grip?: { front: number; rear: number; surface: Readonly<Record<string, readonly [front: number, rear: number]>> };
  /** Scale of the quadratic aerodynamic drag term. Absent: 1. */
  drag?: number;
  /** Friction circle on the driven rear wheels: drive force uses up side grip, so power on a loose surface steps the
   *  tail out (power-on oversteer). Side grip blends from 1 at `knee` (drive force / μFz) to `floor` at knee + span.
   *  Absent: no coupling. */
  frictionCircle?: { floor: number; knee: number; span: number };
  /** Airborne pitch: damping of the pitch rate (1/s) with all wheels off the ground, and the throttle/brake pitch
   *  authority (rad/s², throttle lifts the nose like spinning wheels do, brake drops it). Absent: none. */
  airPitch?: { damping: number; control: number };
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
  front: [0, 1],
  steered: [0, 1],
  driven: [2, 3],
  topSpeed: 27,
  reverseSpeed: 8,
  engineForce: 3200,
  enginePower: 145000,
  brakeForce: 100,
  maxSteer: 0.55,
  steerHalfSpeed: 10,
  steerRate: 2.5,
  suspensionStiffness: 38,
  suspensionCompression: 4.8,
  suspensionRelaxation: 5.5,
  suspensionTravel: 0.22,
  maxSuspensionForce: 18000,
  rollTransfer: 0.55,
  frontTorque: 0,
  curbFeel: 1,
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
  front: [0],
  steered: [0],
  driven: [1],
  topSpeed: 13.8,
  reverseSpeed: 2,
  engineForce: 600,
  enginePower: 6000,
  brakeForce: 5,
  maxSteer: 0.7,
  steerHalfSpeed: 6,
  steerRate: 3.2,
  suspensionStiffness: 45,
  suspensionCompression: 4.8,
  suspensionRelaxation: 5.5,
  suspensionTravel: 0.22,
  maxSuspensionForce: 6000,
  rollTransfer: 0,
  frontTorque: 0,
  curbFeel: 1,
} as const satisfies VehicleTuning;

/** #19-style King of the Hammers 4400 car (~/turku3d/loops/trophy4400/SPEC.md §3): four-wheel steer (42° front and
 *  32° rear inner-wheel lock, passive counter-phase rear steer that tapers to zero and locks out at 30 mph), IFS/IRS
 *  long travel (0.66 m front, 0.71 m rear) on a scheduled dual-rate spring with a hydraulic bump stop and
 *  position-sensitive bypass damping, 4WD with a 40/60 split and ~1000 hp. The chassis origin rides 0.98 m above
 *  the ground at static ride (belly 0.56, SPEC C1; wheelbase 3.16); the hub sits 0.533 m up (connection y −0.03,
 *  static length 0.417). Rapier's `rest` is the longest ray
 *  (full rear droop plus a 3 cm margin); `suspensionStiffness` is the equivalent static rate, so g / (4 k) is the
 *  static sag below `rest` for consumers that still pose the rig from it. */
export const TRUCK_STATIC_LENGTH = 0.417;
const TRUCK_REST = 0.777;
export const TRUCK = {
  halfExt: { x: 2.20, y: 0.42, z: 0.92 },
  mass: 2350,
  comDrop: 0.20,
  comAhead: 0.03,
  wheelRadius: 0.533,
  rest: TRUCK_REST,
  wheels: [
    { x: 1.58, y: -0.03, z: -1.09 }, { x: 1.58, y: -0.03, z: 1.09 },
    { x: -1.58, y: -0.03, z: -1.09 }, { x: -1.58, y: -0.03, z: 1.09 },
  ],
  front: [0, 1],
  steered: [0, 1],
  driven: [0, 1, 2, 3],
  topSpeed: 52,
  reverseSpeed: 9,
  engineForce: 4400,
  enginePower: 746000,
  brakeForce: 120,
  maxSteer: 42 * Math.PI / 180,
  steerHalfSpeed: 11,
  steerRate: 2.4,
  suspensionStiffness: 9.81 / (4 * (TRUCK_REST - TRUCK_STATIC_LENGTH)),
  suspensionCompression: 1.05,
  suspensionRelaxation: 2.6,
  suspensionTravel: TRUCK_REST - (TRUCK_STATIC_LENGTH - 0.38),
  maxSuspensionForce: 60000,
  rollTransfer: 0.7,
  frontTorque: 0.4,
  curbFeel: 0.2,
  rearSteer: {
    wheels: [2, 3], maxAngle: 32 * Math.PI / 180, fullBelow: 2.5, lockout: 13.4, unlock: 11.6, rate: 1.4, manualRate: 0.9, frontLock: 42 * Math.PI / 180,
  },
  suspensionCurve: {
    staticLength: TRUCK_STATIC_LENGTH,
    axles: [
      { wheels: [0, 1], bump: 0.36, droop: 0.30, frequency: 1.10 },
      { wheels: [2, 3], bump: 0.38, droop: 0.33, frequency: 1.20 },
    ],
  },
  grip: {
    front: 1.2, rear: 1.1,
    surface: { gravel: [0.72, 0.66], sand: [0.72, 0.66], soil: [0.68, 0.62], grass: [0.68, 0.62] },
  },
  drag: 0.8,
  frictionCircle: { floor: 0.45, knee: 0.85, span: 0.5 },
  airPitch: { damping: 1.5, control: 1.2 },
} as const satisfies VehicleTuning;

export type VehicleKind = 'car' | 'scooter' | 'truck';
export const VEHICLES: Record<VehicleKind, VehicleTuning> = { car: CAR, scooter: SCOOTER, truck: TRUCK };
/** Target top speed of the scooter (45 km/h); the engine limit sits above it because drag balances the taper. */
export const SCOOTER_TARGET_SPEED = 12.5;
/** Target top speed of the trophy truck (155 km/h, SPEC §3.4). */
export const TRUCK_TARGET_SPEED = 155 / 3.6;
