// Headless car implementation: no renderer, browser globals or input devices.
import type * as Rapier from '@dimforge/rapier3d';
export type RapierModule = typeof Rapier;
type RapierApi = Pick<RapierModule, 'RigidBodyDesc' | 'ColliderDesc' | 'QueryFilterFlags' | 'Ray'>;
import type { V3 } from '../core/origin.ts';
import type { VehicleInput, VehicleState } from '../physics/protocol.ts';
import { VEHICLES, type VehicleKind, type VehicleTuning } from '../physics/constants.ts';
import { GROUP } from '../shared/col/materials.ts';
import { BumpDetector } from './bumps.ts';
import { curbResponse, isCurbMaterial } from './curbStrike.ts';
import { handling } from './handling.ts';
import { rotate } from './math.ts';

import type { WheelQueries } from './curbQueries.ts';
const group = (GROUP.VEHICLE << 16) | GROUP.STATIC_SOLID | GROUP.DYNAMIC;
/** Contact impulse, as a speed change of the whole vehicle (m/s), that counts as a collision. */
export const IMPACT_MIN_DELTA_V = 0.5;

export class Vehicle {
  readonly body: Rapier.RigidBody;
  readonly controller: Rapier.DynamicRayCastVehicleController;
  readonly kind: VehicleKind;
  readonly tuning: VehicleTuning;
  private contacts = new Map<string, V3>();
  private strikeId = 0;
  private lastStrike: VehicleState['curbStrike'] = null;
  private steering = 0;
  private impactId = 0;
  private impact: VehicleState['impact'] = null;
  private impactCooldown = 0;
  private readonly drive: number[];
  /** Velocity once every wheel, drag and curb impulse of the tick is applied: what the world step then changes. */
  private beforeSolve: V3 | null = null;
  private bumps = new BumpDetector();

  private R: RapierApi;
  private world: Rapier.World;
  private surfaces: WheelQueries;

  constructor(R: RapierApi, world: Rapier.World, pos: V3, yaw: number, surfaces: WheelQueries,
    spec: { kind?: VehicleKind; mass?: number; halfExt?: V3; parked?: boolean } = {}) {
    this.R = R; this.world = world; this.surfaces = surfaces;
    this.kind = spec.kind ?? 'car';
    const t = this.tuning = VEHICLES[this.kind];
    this.body = world.createRigidBody(R.RigidBodyDesc.dynamic().setTranslation(pos.x, pos.y, pos.z)
      .setRotation({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) })
      .setCanSleep(true).setCcdEnabled(true).setAngularDamping(1.2));
    const h = spec.halfExt ?? t.halfExt, mass = spec.mass ?? t.mass;
    const chassis = R.ColliderDesc.cuboid(h.x, h.y, h.z).setFriction(0.5);
    // A lowered centre of mass keeps the narrow outrigger layout upright; inertia stays the box's own.
    if (t.comDrop) {
      chassis.setMassProperties(mass, { x: 0, y: -t.comDrop, z: 0 }, {
        x: mass * (h.y * h.y + h.z * h.z) / 3, y: mass * (h.x * h.x + h.z * h.z) / 3, z: mass * (h.x * h.x + h.y * h.y) / 3,
      }, { x: 0, y: 0, z: 0, w: 1 });
    } else chassis.setMass(mass);
    world.createCollider(chassis
      .setCollisionGroups((GROUP.VEHICLE << 16) | GROUP.STATIC_SOLID | GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC | 0xff00), this.body);
    const v = this.controller = world.createVehicleController(this.body);
    v.indexUpAxis = 1;
    // Rapier exposes this setter under the name setIndexForwardAxis.
    v.setIndexForwardAxis = 0;
    for (const w of t.wheels) v.addWheel(w, { x: 0, y: -1, z: 0 }, { x: 0, y: 0, z: 1 }, t.rest, t.wheelRadius);
    for (let i = 0; i < t.wheels.length; i++) {
      v.setWheelSuspensionStiffness(i, t.suspensionStiffness);
      v.setWheelSuspensionCompression(i, t.suspensionCompression);
      v.setWheelSuspensionRelaxation(i, t.suspensionRelaxation);
      v.setWheelMaxSuspensionTravel(i, t.suspensionTravel);
      v.setWheelMaxSuspensionForce(i, t.maxSuspensionForce);
      v.setWheelSideFrictionStiffness(i, 1.1);
    }
    // Engine force per driven wheel: the front share on the driven steered wheels, the rest on the driven rear ones.
    const front = t.driven.filter(i => t.steered.includes(i)).length, rear = t.driven.length - front;
    this.drive = t.wheels.map((_, i) => !t.driven.includes(i) ? 0
      : t.steered.includes(i) ? t.frontTorque * t.driven.length / front : (1 - t.frontTorque) * t.driven.length / rear);
    if (spec.parked) this.body.sleep();
  }

  reset(pos: V3, yaw: number) {
    this.body.setTranslation(pos, true);
    this.body.setRotation({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) }, true);
    this.body.setLinvel({ x: 0, y: 0, z: 0 }, true);
    this.body.setAngvel({ x: 0, y: 0, z: 0 }, true);
    this.contacts.clear(); this.lastStrike = null; this.steering = 0; this.impact = null; this.impactCooldown = 0;
    this.beforeSolve = null;
    this.bumps.reset();
  }

  step(dt: number, input: VehicleInput | null) {
    this.beforeSolve = null;
    if (!input && this.body.isSleeping()) return;
    const v = this.controller, b = this.body;
    const f = rotate({ x: 1, y: 0, z: 0 }, b.rotation()), lv = b.linvel();
    const speed = lv.x * f.x + lv.y * f.y + lv.z * f.z;
    const t = this.tuning, h = handling(input, speed, t);
    this.steering += Math.max(-dt * t.steerRate, Math.min(dt * t.steerRate, h.steer - this.steering));
    for (let i = 0; i < t.wheels.length; i++) {
      const steered = t.steered.includes(i), driven = t.driven.includes(i);
      v.setWheelSteering(i, steered ? this.steering : 0);
      v.setWheelEngineForce(i, h.engine * this.drive[i]);
      // The handbrake locks the driven rear wheels only.
      v.setWheelBrake(i, h.brake + (driven && !steered && h.handbrake ? t.brakeForce : 0));
      v.setWheelFrictionSlip(i, steered ? h.frontGrip : h.rearGrip);
    }
    v.updateVehicle(dt, this.R.QueryFilterFlags.EXCLUDE_SENSORS, group);
    // Quadratic aerodynamic drag gives a soft 25 m/s terminal speed, including downhill coasting.
    const vel = b.linvel(), horizontal = Math.hypot(vel.x, vel.z);
    const drag = Math.min(0.15, (0.015 + horizontal * 0.004) * dt);
    b.applyImpulse({ x: -vel.x * b.mass() * drag, y: 0, z: -vel.z * b.mass() * drag }, true);
    this.strikeCurbs(dt, input);
    this.detectBump(dt);
    this.beforeSolve = { ...b.linvel() };
  }

  /** Feeds every wheel's suspension length and ground contact height to the bump detector (bumps.ts). */
  private detectBump(dt: number) {
    const v = this.controller, t = this.tuning, lv = this.body.linvel();
    this.bumps.update(dt, Math.hypot(lv.x, lv.z), t.wheels.map((_, i) => ({
      length: v.wheelSuspensionLength(i) ?? t.rest,
      ground: v.wheelIsInContact(i) ? v.wheelContactPoint(i)?.y ?? null : null,
    })));
  }

  private strikeCurbs(dt: number, input: VehicleInput | null) {
    const b = this.body, q = b.rotation(), p = b.translation(), velocity = b.linvel();
    const speed = Math.hypot(velocity.x, velocity.z);
    const forward = rotate({ x: 1, y: 0, z: 0 }, q);
    const sign = (input?.throttle ?? 0) < 0 || ((input?.brake ?? 0) > 0 && speed < 0.4) ? -1 : 1;
    const len = Math.hypot(forward.x, forward.z) || 1;
    const dir = speed > 0.1 ? { x: velocity.x / speed, y: 0, z: velocity.z / speed } : { x: sign * forward.x / len, y: 0, z: sign * forward.z / len };
    const distance = Math.max(0.06, speed * dt + 0.025);
    for (const [key, old] of this.contacts) {
      if (Math.hypot(p.x - old.x, p.z - old.z) > 0.9) this.contacts.delete(key);
    }
    const t = this.tuning;
    for (let i = 0; i < t.wheels.length; i++) {
      const wheel = t.wheels[i];
      const offset = rotate({ x: wheel.x, y: wheel.y - (this.controller.wheelSuspensionLength(i) ?? t.rest), z: wheel.z }, q);
      const center = { x: p.x + offset.x, y: p.y + offset.y + 0.015, z: p.z + offset.z };
      const hit = this.surfaces.castWheel(center, dir, distance, t.wheelRadius);
      if (!hit || !isCurbMaterial(hit.material)) continue;
      // Measure the lower road beside the face: suspension compression and chassis roll must
      // not inflate the step height, particularly for the rear axle after the front jolt.
      const groundRay = new this.R.Ray({ x: hit.point.x - dir.x * 0.12, y: hit.top + 0.5, z: hit.point.z - dir.z * 0.12 }, { x: 0, y: -1, z: 0 });
      const ground = this.world.castRay(groundRay, 20, true, this.R.QueryFilterFlags.EXCLUDE_SENSORS, group, undefined, b);
      if (!ground) continue;
      const height = ground.timeOfImpact - 0.5;
      if (height < 0.03 || hit.normal.y > 0.85) continue;
      const response = curbResponse(speed, height, b.mass(), t.curbFeel);
      const key = `${i}:${hit.handle}`;
      if (response.blocked) {
        // A persistent wheel barrier. Re-evaluated after engine forces each tick, even at rest.
        const n = hit.normal, nl = Math.hypot(n.x, n.z);
        if (nl < 0.2) continue;
        const now = b.linvel(), into = (now.x * n.x + now.z * n.z) / nl;
        if (into < 0) b.applyImpulse({ x: -into * n.x / nl * b.mass(), y: 0, z: -into * n.z / nl * b.mass() }, true);
      } else if (!this.contacts.has(key) && speed > 0.2) {
        b.applyImpulse({ x: -velocity.x * response.loss * b.mass(), y: 0, z: -velocity.z * response.loss * b.mass() }, true);
        b.applyImpulseAtPoint({ x: 0, y: response.liftImpulse, z: 0 }, { x: center.x, y: center.y, z: center.z }, true);
      } else continue;
      if (!this.contacts.has(key)) {
        this.lastStrike = { id: ++this.strikeId, wheel: i, height, speed, loss: response.loss, blocked: response.blocked, rumble: response.rumble };
        this.contacts.set(key, { ...p });
      }
    }
  }

  /** After each fixed world step: the collision impulse is the chassis momentum the step changed beyond gravity.
   *  Rapier reports no contact impulses between the chassis and trimesh walls (every building in the tiles), so the
   *  manifolds cannot measure crashes; resting contacts change nothing and stay quiet. */
  afterStep(dt: number) {
    const before = this.beforeSolve;
    if (!before || this.body.isSleeping()) return;
    this.impactCooldown = Math.max(0, this.impactCooldown - dt);
    const v = this.body.linvel(), g = this.world.gravity.y * dt;
    const impulse = Math.hypot(v.x - before.x, v.y - before.y - g, v.z - before.z) * this.body.mass();
    if (impulse > IMPACT_MIN_DELTA_V * this.body.mass() && this.impactCooldown === 0) {
      this.impact = { id: ++this.impactId, impulse };
      this.impactCooldown = 0.15;
    }
  }

  state(): VehicleState {
    const b = this.body, v = this.controller, lv = b.linvel();
    const forward = rotate({ x: 1, y: 0, z: 0 }, b.rotation());
    return { kind: this.kind, pos: { ...b.translation() }, rot: { ...b.rotation() }, linvel: { ...lv }, speed: Math.hypot(lv.x, lv.y, lv.z),
      forwardSpeed: lv.x * forward.x + lv.y * forward.y + lv.z * forward.z,
      curbStrike: this.lastStrike, impact: this.impact, bump: this.bumps.latest,
      wheels: this.tuning.wheels.map((cp, i) => {
        const suspension = v.wheelSuspensionLength(i) ?? this.tuning.rest;
        return { pos: { x: cp.x, y: cp.y - suspension, z: cp.z }, steer: v.wheelSteering(i) ?? 0,
          rotation: v.wheelRotation(i) ?? 0, contact: v.wheelIsInContact(i), suspension };
      }) };
  }
}
