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

export const CAR = {
  halfExt: { x: 2.1, y: 0.3, z: 0.9 }, // forward = +X in the chassis frame
  mass: 1200,
  wheelRadius: 0.33,
  rest: 0.3,
  wheels: [
    { x: 1.35, y: -0.15, z: -0.8 }, { x: 1.35, y: -0.15, z: 0.8 },
    { x: -1.35, y: -0.15, z: -0.8 }, { x: -1.35, y: -0.15, z: 0.8 },
  ],
  topSpeed: 27,
  engineForce: 3200, // N per driven (rear) wheel
  brakeForce: 100,
  maxSteer: 0.55,
} as const;
