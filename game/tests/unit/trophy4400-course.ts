// Headless Rapier course for the 4400 truck acceptance numbers (SPEC §7 Dynamics). Shared by
// trophy4400-dynamics.test.ts; every scenario runs the real `Vehicle` on its own small world.
import * as R from '@dimforge/rapier3d';
import { DT, TRUCK } from '../../src/physics/constants';
import type { VehicleInput, VehicleState } from '../../src/physics/protocol';
import { CurbQueries } from '../../src/vehicle/curbQueries';
import type { RearSteerMode } from '../../src/vehicle/rearSteer';
import { Vehicle } from '../../src/vehicle/vehicle';
import { rotate } from '../../src/vehicle/math';
import { GROUP, MAT } from '../../src/shared/col/materials';

const groups = (GROUP.STATIC_SOLID << 16) | GROUP.VEHICLE;
export const G = 9.81;
export const deg = (rad: number) => rad * 180 / Math.PI;

export interface CourseOptions {
  /** Ground material (MAT id), e.g. MAT.road or MAT.soil. */
  material?: number;
  /** Extra colliders: called with the world before the truck spawns. */
  build?: (w: R.World) => void;
  /** Chassis origin spawn height above the ground; default static ride + 2 cm. */
  y?: number;
  x?: number;
  yaw?: number;
  settle?: number;
}

export function course(o: CourseOptions = {}) {
  const w = new R.World({ x: 0, y: -G, z: 0 });
  w.timestep = DT;
  const ground = w.createCollider(R.ColliderDesc.cuboid(3000, 0.5, 3000).setTranslation(0, -0.5, 0).setCollisionGroups(groups));
  o.build?.(w);
  const material = o.material ?? MAT.road;
  const vehicle = new Vehicle(R, w, { x: o.x ?? 0, y: o.y ?? 1.0, z: 0 }, o.yaw ?? 0, new CurbQueries(R, w), { kind: 'truck' },
    () => material);
  const chassis = vehicle.body.collider(0);
  let chassisContact = false, time = 0;
  const touching = () => {
    let hit = false;
    w.contactPairsWith(chassis, other => {
      w.contactPair(chassis, other, manifold => { if (manifold.numContacts() > 0) hit = true; });
    });
    return hit;
  };
  const step = (input: VehicleInput) => {
    vehicle.step(DT, input); w.step(); vehicle.afterStep(DT); time += DT;
    if (touching()) chassisContact = true;
    return vehicle.state();
  };
  const run = (seconds: number, input: VehicleInput | ((s: VehicleState, t: number) => VehicleInput),
    each?: (s: VehicleState, t: number) => boolean | void) => {
    let s = vehicle.state();
    for (let f = 0; f < Math.round(seconds / DT); f++) {
      s = step(typeof input === 'function' ? input(s, f * DT) : input);
      if (each?.(s, f * DT) === true) break;
    }
    return s;
  };
  if (o.settle !== 0) run(o.settle ?? 2.5, idle());
  chassisContact = false;
  return { w, ground, vehicle, step, run, get chassisContact() { return chassisContact; }, resetContact() { chassisContact = false; },
    get time() { return time; }, free: () => w.free() };
}

export const idle = (mode?: RearSteerMode): VehicleInput => ({ throttle: 0, brake: 0, steer: 0, handbrake: false, ...(mode ? { rearMode: mode } : {}) });

/** Simple cruise control around `target` m/s. */
export function cruise(s: VehicleState, target: number, steer = 0, mode?: RearSteerMode): VehicleInput {
  const e = target - s.forwardSpeed;
  return { throttle: Math.max(0, Math.min(1, e * 0.6 + 0.08)), brake: e < -1 ? Math.min(1, -e * 0.3) : 0, steer, handbrake: false,
    ...(mode ? { rearMode: mode } : {}) };
}

/** Chassis forward, right and up axes. */
export function axes(q: { x: number; y: number; z: number; w: number }) {
  return { forward: rotate({ x: 1, y: 0, z: 0 }, q), right: rotate({ x: 0, y: 0, z: 1 }, q), up: rotate({ x: 0, y: 1, z: 0 }, q) };
}
/** Roll (deg, + right side down) and pitch (deg, + nose up). */
export const roll = (q: VehicleState['rot']) => deg(Math.asin(Math.max(-1, Math.min(1, -axes(q).right.y))));
export const pitch = (q: VehicleState['rot']) => deg(Math.asin(Math.max(-1, Math.min(1, axes(q).forward.y))));
export const heading = (q: VehicleState['rot']) => { const f = axes(q).forward; return Math.atan2(-f.z, f.x); };

/** Compression from static of wheel i as a fraction of its bump (+) or droop (−). */
export function travel(s: VehicleState, i: number) {
  const curve = TRUCK.suspensionCurve, axle = curve.axles.find(a => (a.wheels as readonly number[]).includes(i))!;
  const b = curve.staticLength - s.wheels[i].suspension;
  return b >= 0 ? b / axle.bump : b / axle.droop;
}

/** A static wedge ramp rising `height` over `length` along +X, ending at x = `end`, across z ∈ [z0, z1]. */
export function ramp(w: R.World, end: number, height: number, length: number, z0 = -6, z1 = 6) {
  const a = Math.atan2(height, length), slope = Math.hypot(height, length), t = 0.2;
  const cx = end - length / 2 + Math.sin(a) * t / 2, cy = height / 2 - Math.cos(a) * t / 2;
  return w.createCollider(R.ColliderDesc.cuboid(slope / 2, t / 2, (z1 - z0) / 2).setTranslation(cx, cy, (z0 + z1) / 2)
    .setRotation({ x: 0, y: 0, z: Math.sin(a / 2), w: Math.cos(a / 2) }).setCollisionGroups(groups));
}
/** A static wedge falling `height` over `length` along +X from its top at x = `start`, across z ∈ [z0, z1]. */
export function descent(w: R.World, start: number, height: number, length: number, z0 = -6, z1 = 6) {
  const a = Math.atan2(height, length), slope = Math.hypot(height, length), t = 0.2;
  const cx = start + length / 2 - Math.sin(a) * t / 2, cy = height / 2 - Math.cos(a) * t / 2;
  return w.createCollider(R.ColliderDesc.cuboid(slope / 2, t / 2, (z1 - z0) / 2).setTranslation(cx, cy, (z0 + z1) / 2)
    .setRotation({ x: 0, y: 0, z: -Math.sin(a / 2), w: Math.cos(a / 2) }).setCollisionGroups(groups));
}
export const box = (w: R.World, x: number, y: number, z: number, hx: number, hy: number, hz: number) =>
  w.createCollider(R.ColliderDesc.cuboid(hx, hy, hz).setTranslation(x, y, z).setCollisionGroups(groups));
