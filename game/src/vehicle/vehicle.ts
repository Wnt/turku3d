// Headless car implementation: no renderer, browser globals or input devices.
import type * as Rapier from '@dimforge/rapier3d';
import type { V3 } from '../core/origin.ts';
import type { VehicleInput, VehicleState } from '../physics/protocol.ts';
import { CAR } from '../physics/constants.ts';
import { GROUP } from '../shared/col/materials.ts';
import { curbResponse, isCurbMaterial } from './curbStrike.ts';
import { handling } from './handling.ts';
import { rotate } from './math.ts';

import type { WheelQueries } from './curbQueries.ts';
const group = (GROUP.VEHICLE << 16) | GROUP.STATIC_SOLID | GROUP.DYNAMIC;

export class Vehicle {
  readonly body: Rapier.RigidBody;
  readonly controller: Rapier.DynamicRayCastVehicleController;
  private contacts = new Map<string, V3>();
  private strikeId = 0;
  private lastStrike: VehicleState['curbStrike'] = null;
  private steering = 0;
  private impactId = 0;
  private impact: VehicleState['impact'] = null;
  private impactCooldown = 0;

  private R: typeof Rapier;
  private world: Rapier.World;
  private surfaces: WheelQueries;

  constructor(R: typeof Rapier, world: Rapier.World, pos: V3, yaw: number, surfaces: WheelQueries) {
    this.R = R; this.world = world; this.surfaces = surfaces;
    this.body = world.createRigidBody(R.RigidBodyDesc.dynamic().setTranslation(pos.x, pos.y, pos.z)
      .setRotation({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) })
      .setCanSleep(false).setCcdEnabled(true).setAngularDamping(1.2));
    const h = CAR.halfExt;
    world.createCollider(R.ColliderDesc.cuboid(h.x, h.y, h.z).setMass(CAR.mass).setFriction(0.5)
      .setCollisionGroups((GROUP.VEHICLE << 16) | GROUP.STATIC_SOLID | GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC | 0xff00), this.body);
    const v = this.controller = world.createVehicleController(this.body);
    v.indexUpAxis = 1;
    // Rapier exposes this setter under the name setIndexForwardAxis.
    v.setIndexForwardAxis = 0;
    for (const w of CAR.wheels) v.addWheel(w, { x: 0, y: -1, z: 0 }, { x: 0, y: 0, z: 1 }, CAR.rest, CAR.wheelRadius);
    for (let i = 0; i < 4; i++) {
      v.setWheelSuspensionStiffness(i, 38);
      v.setWheelSuspensionCompression(i, 4.8);
      v.setWheelSuspensionRelaxation(i, 5.5);
      v.setWheelMaxSuspensionTravel(i, 0.22);
      v.setWheelMaxSuspensionForce(i, 18000);
      v.setWheelSideFrictionStiffness(i, 1.1);
    }
  }

  reset(pos: V3, yaw: number) {
    this.body.setTranslation(pos, true);
    this.body.setRotation({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) }, true);
    this.body.setLinvel({ x: 0, y: 0, z: 0 }, true);
    this.body.setAngvel({ x: 0, y: 0, z: 0 }, true);
    this.contacts.clear(); this.lastStrike = null; this.steering = 0; this.impact = null; this.impactCooldown = 0;
  }

  step(dt: number, input: VehicleInput | null) {
    const v = this.controller, b = this.body;
    const f = rotate({ x: 1, y: 0, z: 0 }, b.rotation()), lv = b.linvel();
    const speed = lv.x * f.x + lv.y * f.y + lv.z * f.z;
    const h = handling(input, speed);
    this.steering += Math.max(-dt * 2.5, Math.min(dt * 2.5, h.steer - this.steering));
    for (let i = 0; i < 4; i++) {
      v.setWheelSteering(i, i < 2 ? this.steering : 0);
      v.setWheelEngineForce(i, i < 2 ? 0 : h.engine);
      v.setWheelBrake(i, h.brake + (i >= 2 && h.handbrake ? CAR.brakeForce : 0));
      v.setWheelFrictionSlip(i, i < 2 ? h.frontGrip : h.rearGrip);
    }
    v.updateVehicle(dt, this.R.QueryFilterFlags.EXCLUDE_SENSORS, group);
    // Quadratic aerodynamic drag gives a soft 25 m/s terminal speed, including downhill coasting.
    const vel = b.linvel(), horizontal = Math.hypot(vel.x, vel.z);
    const drag = Math.min(0.15, (0.015 + horizontal * 0.004) * dt);
    b.applyImpulse({ x: -vel.x * b.mass() * drag, y: 0, z: -vel.z * b.mass() * drag }, true);
    this.strikeCurbs(dt, input);
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
    for (let i = 0; i < 4; i++) {
      const wheel = CAR.wheels[i];
      const offset = rotate({ x: wheel.x, y: wheel.y - (this.controller.wheelSuspensionLength(i) ?? CAR.rest), z: wheel.z }, q);
      const center = { x: p.x + offset.x, y: p.y + offset.y + 0.015, z: p.z + offset.z };
      const hit = this.surfaces.castWheel(center, dir, distance);
      if (!hit || !isCurbMaterial(hit.material)) continue;
      // Measure the lower road beside the face: suspension compression and chassis roll must
      // not inflate the step height, particularly for the rear axle after the front jolt.
      const groundRay = new this.R.Ray({ x: hit.point.x - dir.x * 0.12, y: hit.top + 0.5, z: hit.point.z - dir.z * 0.12 }, { x: 0, y: -1, z: 0 });
      const ground = this.world.castRay(groundRay, 20, true, this.R.QueryFilterFlags.EXCLUDE_SENSORS, group, undefined, b);
      if (!ground) continue;
      const height = ground.timeOfImpact - 0.5;
      if (height < 0.03 || hit.normal.y > 0.85) continue;
      const response = curbResponse(speed, height, b.mass());
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

  /** Read solver impulses after each fixed world step; resting contacts stay below the threshold. */
  afterStep(dt: number) {
    this.impactCooldown = Math.max(0, this.impactCooldown - dt);
    const chassis = this.body.collider(0);
    let impulse = 0;
    this.world.contactPairsWith(chassis, other => {
      this.world.contactPair(chassis, other, manifold => {
        for (let i = 0; i < manifold.numContacts(); i++) impulse += manifold.contactImpulse(i);
      });
    });
    if (impulse > 600 && this.impactCooldown === 0) {
      this.impact = { id: ++this.impactId, impulse };
      this.impactCooldown = 0.15;
    }
  }

  state(): VehicleState {
    const b = this.body, v = this.controller, lv = b.linvel();
    const forward = rotate({ x: 1, y: 0, z: 0 }, b.rotation());
    return { pos: { ...b.translation() }, rot: { ...b.rotation() }, linvel: { ...lv }, speed: Math.hypot(lv.x, lv.y, lv.z),
      forwardSpeed: lv.x * forward.x + lv.y * forward.y + lv.z * forward.z,
      curbStrike: this.lastStrike, impact: this.impact,
      wheels: CAR.wheels.map((cp, i) => {
        const suspension = v.wheelSuspensionLength(i) ?? CAR.rest;
        return { pos: { x: cp.x, y: cp.y - suspension, z: cp.z }, steer: v.wheelSteering(i) ?? 0,
          rotation: v.wheelRotation(i) ?? 0, contact: v.wheelIsInContact(i), suspension };
      }) };
  }
}
