// Small synthetic fixtures, shared with the Node validator. No real tile data.
import type * as Rapier from '@dimforge/rapier3d';
import { CurbQueries } from '../../src/vehicle/curbQueries.ts';
import { Vehicle } from '../../src/vehicle/vehicle.ts';
import { MAT, GROUP } from '../../src/shared/col/materials.ts';
import { isCurbMaterial } from '../../src/vehicle/curbStrike.ts';

export const CURB_SCENARIOS = [
  ...[0.07, 0.12, 0.15].flatMap(height => [3, 6, 10].map(speed => ({ height, speed, blocked: false }))),
  ...[0.26, 0.3, 0.4].map(height => ({ height, speed: 2, blocked: true })),
];

export function vehicleFixture(R: typeof Rapier, height: number, material: number = MAT.curb, oneWheel = false, depth = 80) {
  const world = new R.World({ x: 0, y: -9.81, z: 0 });
  world.timestep = 1 / 60;
  const groups = (GROUP.STATIC_SOLID << 16) | GROUP.VEHICLE;
  world.createCollider(R.ColliderDesc.cuboid(600, 0.5, 30).setTranslation(0, -0.5, 0).setCollisionGroups(groups));
  const curb = world.createCollider(R.ColliderDesc.cuboid(depth / 2, height / 2, oneWheel ? 0.5 : 10)
    .setTranslation(depth / 2, height / 2, oneWheel ? -0.8 : 0).setCollisionGroups(groups));
  const surfaces = new CurbQueries(R, world);
  if (isCurbMaterial(material)) surfaces.add(curb, curb.shape, () => ({ material, top: height }));
  const vehicle = new Vehicle(R, world, { x: -5, y: 0.8, z: 0 }, 0, surfaces);
  for (let i = 0; i < 90; i++) { vehicle.step(1 / 60, null); world.step(); }
  return { world, vehicle };
}

/** Identical acceptance measurements for browser-WASM unit tests and native Node/compat validation. */
export function runCurbScenario(R: typeof Rapier, scenario: typeof CURB_SCENARIOS[number]) {
  const coast = { throttle: 0, brake: 0, steer: 0, handbrake: false };
  const { world, vehicle } = vehicleFixture(R, scenario.height);
  try {
    vehicle.body.setLinvel({ x: scenario.speed, y: 0, z: 0 }, true);
    let maxPitch = 0, before = 0, after = 0, strikes = 0, previousId = 0;
    let previous = vehicle.state();
    const steps = scenario.blocked ? 300 : Math.ceil(10 / scenario.speed * 60);
    for (let i = 0; i < steps; i++) {
      vehicle.step(1 / 60, scenario.blocked && previous.curbStrike?.blocked ? { ...coast, throttle: 0.25 } : coast);
      world.step();
      const s = vehicle.state();
      const pitch = Math.abs(Math.asin(Math.max(-1, Math.min(1, 2 * (s.rot.w * s.rot.z + s.rot.x * s.rot.y))))) * 180 / Math.PI;
      if (s.curbStrike) maxPitch = Math.max(maxPitch, pitch);
      if (s.curbStrike && s.curbStrike.id !== previousId) {
        if (!before) before = Math.hypot(previous.linvel.x, previous.linvel.z);
        after = Math.hypot(s.linvel.x, s.linvel.z);
        strikes = s.curbStrike.id; previousId = strikes;
      }
      previous = s;
    }
    return { ...scenario, maxPitch, before, after, loss: 1 - after / before, strikes, state: vehicle.state() };
  } finally { world.free(); }
}
