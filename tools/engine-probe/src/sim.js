// Physics-only scenario (runs identically in browser and in Node).
// Rapier: one trimesh static collider, a kinematic capsule character
// (KinematicCharacterController with autostep + snap-to-ground + slope limits)
// and a DynamicRayCastVehicleController car.

import { buildLevel } from './level.js';

export const DT = 1 / 60;

export function createSim(RAPIER, opts = {}) {
  const level = buildLevel();
  const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  world.timestep = DT;

  const flags = RAPIER.TriMeshFlags.FIX_INTERNAL_EDGES; // merges dup verts + fixes ghost bumps
  const groundDesc = RAPIER.ColliderDesc.trimesh(level.positions, level.indices, flags).setFriction(1.0);
  const ground = world.createCollider(groundDesc);

  // ---------------- Character ----------------
  const R = 0.3, HH = 0.6; // capsule radius / half-height  -> 1.8 m tall
  const makeWalker = (name, z) => {
    const body = world.createRigidBody(
      RAPIER.RigidBodyDesc.kinematicPositionBased().setTranslation(-3, HH + R + 0.05, z),
    );
    const collider = world.createCollider(RAPIER.ColliderDesc.capsule(HH, R), body);
    return { name, body, collider, vy: 0, grounded: false, log: [], z };
  };
  const walkers = [makeWalker('stairs', 0), makeWalker('ramp15', 3.5), makeWalker('ramp55', 7.5)];

  const cc = world.createCharacterController(0.02); // 2 cm skin
  cc.setUp({ x: 0, y: 1, z: 0 });
  cc.enableAutostep(0.25, 0.15, false); // climb steps <= 25 cm with >= 15 cm free tread
  cc.enableSnapToGround(0.3);
  cc.setMaxSlopeClimbAngle((45 * Math.PI) / 180);
  cc.setMinSlopeSlideAngle((50 * Math.PI) / 180);
  cc.setSlideEnabled(true);
  cc.setApplyImpulsesToDynamicBodies(true);

  const WALK_SPEED = opts.walkSpeed ?? 1.6; // m/s

  function stepWalker(w) {
    if (w.grounded) w.vy = -0.5; // keep pressing into ground so snap/grounded is stable
    else w.vy -= 9.81 * DT;
    const tx = w.body.translation().x;
    const desired = { x: tx < 13 ? WALK_SPEED * DT : 0, y: w.vy * DT, z: 0 };
    cc.computeColliderMovement(w.collider, desired);
    const m = cc.computedMovement();
    w.grounded = cc.computedGrounded();
    const t = w.body.translation();
    w.body.setNextKinematicTranslation({ x: t.x + m.x, y: t.y + m.y, z: t.z + m.z });
  }

  // ---------------- Vehicle ----------------
  const chassisDesc = RAPIER.RigidBodyDesc.dynamic()
    .setTranslation(-15, 1.0, -6)
    .setCanSleep(false)
    .setAdditionalMass(0);
  const chassis = world.createRigidBody(chassisDesc);
  // 4.2 m x 0.6 m x 1.8 m box, ~1200 kg
  const halfExt = { x: 2.1, y: 0.3, z: 0.9 };
  const vol = 8 * halfExt.x * halfExt.y * halfExt.z;
  world.createCollider(
    RAPIER.ColliderDesc.cuboid(halfExt.x, halfExt.y, halfExt.z).setDensity(1200 / vol).setFriction(0.5),
    chassis,
  );
  const car = world.createVehicleController(chassis);
  const WR = 0.33; // wheel radius (typical 16" wheel + tyre)
  const wheelPts = [
    { x: 1.35, y: -0.15, z: -0.8 },
    { x: 1.35, y: -0.15, z: 0.8 },
    { x: -1.35, y: -0.15, z: -0.8 },
    { x: -1.35, y: -0.15, z: 0.8 },
  ];
  for (const p of wheelPts) {
    car.addWheel(p, { x: 0, y: -1, z: 0 }, { x: 0, y: 0, z: 1 }, 0.3, WR);
  }
  for (let i = 0; i < 4; i++) {
    car.setWheelSuspensionStiffness(i, 30);
    car.setWheelSuspensionCompression(i, 4.4);
    car.setWheelSuspensionRelaxation(i, 2.3);
    car.setWheelMaxSuspensionTravel(i, 0.25);
    car.setWheelFrictionSlip(i, 2.0);
    car.setWheelSideFrictionStiffness(i, 1.0);
    car.setWheelMaxSuspensionForce(i, 20000);
  }
  const carLog = [];
  const carInput = { throttle: 0, steer: 0, brake: 0 };

  function stepCar() {
    const ENGINE = 1600; // N per driven wheel
    for (let i = 0; i < 4; i++) {
      const front = i < 2;
      car.setWheelSteering(i, front ? carInput.steer : 0);
      car.setWheelEngineForce(i, front ? 0 : carInput.throttle * ENGINE);
      car.setWheelBrake(i, carInput.brake * 30);
    }
    car.updateVehicle(DT);
  }

  let frame = 0;
  function step() {
    for (const w of walkers) stepWalker(w);
    stepCar();
    world.step();
    frame++;
  }

  function sample() {
    const out = { frame, t: +(frame * DT).toFixed(3), walkers: {}, car: null };
    for (const w of walkers) {
      const t = w.body.translation();
      const feetY = t.y - HH - R;
      out.walkers[w.name] = { x: +t.x.toFixed(3), feetY: +feetY.toFixed(3), grounded: w.grounded };
    }
    const c = chassis.translation();
    const lv = chassis.linvel();
    let contacts = 0;
    for (let i = 0; i < 4; i++) if (car.wheelIsInContact(i)) contacts++;
    out.car = {
      x: +c.x.toFixed(3),
      y: +c.y.toFixed(3),
      speed: +Math.hypot(lv.x, lv.z).toFixed(2),
      wheelsInContact: contacts,
      susp: [0, 1, 2, 3].map((i) => +(car.wheelSuspensionLength(i) ?? -1).toFixed(3)),
    };
    return out;
  }

  return { RAPIER, world, level, ground, walkers, cc, chassis, car, carInput, halfExt, WR, wheelPts, step, sample, HH, R, carLog };
}

// Scripted scenario used by both the Node test and headless Chrome test.
export function runScenario(sim, seconds = 14, every = 15) {
  const frames = Math.round(seconds / 1 / (1 / 60));
  const samples = [];
  for (let f = 0; f < frames; f++) {
    const t = f / 60;
    const cx = sim.chassis.translation().x;
    const lv = sim.chassis.linvel();
    const spd = Math.hypot(lv.x, lv.z);
    sim.carInput.throttle = t > 1 && cx < 45 && spd < 6 ? 1 : 0;
    sim.carInput.brake = cx >= 45 ? 1 : 0;
    sim.step();
    if (f % every === 0) samples.push(sim.sample());
  }
  samples.push(sim.sample());
  return { samples, verdict: verdict(sim, samples) };
}

export function verdict(sim, samples) {
  const last = samples[samples.length - 1];
  const L = sim.level;
  const s = last.walkers.stairs, r15 = last.walkers.ramp15, r55 = last.walkers.ramp55;
  const maxCarY = Math.max(...samples.map((x) => x.car.y));
  const crossedCurb = samples.find((x) => x.car.x > 1.0);
  return {
    stairsClimbed: s.x > 9 && Math.abs(s.feetY - L.platformY) < 0.08,
    stairsFinal: s,
    platformY: +L.platformY.toFixed(3),
    ramp15Climbed: r15.x > 10.5 && Math.abs(r15.feetY - L.ramp15TopY) < 0.08,
    ramp15Final: r15,
    ramp55Blocked: r55.x < 4.3 && r55.feetY < 0.6,
    ramp55Final: r55,
    carMountedCurbAndRamp: !!crossedCurb && last.car.x > 27 && last.car.y > L.ramp8TopY,
    carFinal: last.car,
    carMaxY: +maxCarY.toFixed(3),
    deckY: +L.ramp8TopY.toFixed(3),
  };
}
