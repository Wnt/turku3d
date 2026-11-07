import { ackermann, ackermann4 } from './wheelKinematics.ts';
// Headless car implementation: no renderer, browser globals or input devices.
import type * as Rapier from '@dimforge/rapier3d';
export type RapierModule = typeof Rapier;
type RapierApi = Pick<RapierModule, 'RigidBodyDesc' | 'ColliderDesc' | 'QueryFilterFlags' | 'Ray'>;
import type { V3 } from '../core/origin.ts';
import type { VehicleInput, VehicleState } from '../physics/protocol.ts';
import { REMOTE_VEHICLE, VEHICLES, type VehicleKind, type VehicleTuning } from '../physics/constants.ts';
import { GROUP, MATERIALS } from '../shared/col/materials.ts';
import { REAR_STEER_MODES, rearSteerIdle, rearSteerStep, type RearSteerState } from './rearSteer.ts';
import { corners, droopLength, schedule, type Corner } from './suspension.ts';
import { BumpDetector } from './bumps.ts';
import { curbResponse, isCurbMaterial } from './curbStrike.ts';
import { handling } from './handling.ts';
import { rotate } from './math.ts';
import { longitudinalSlip } from './tyreSlip.ts';
import { dot, tyreRollImpulse } from './weightTransfer.ts';
import { applyHit, applyScrape, cageOffset, DamageState, damageLayout, decodeDamage, encodeDamage, stampSurface, type DamageHit, type DamageScrape, type SurfaceFace } from './damage.ts';
import { INTACT, mechanics, type Mechanics } from './mechanics.ts';
import { resetTyres, solveTyres, tyreState, type TyrePatch, type TyreResult, type TyreState } from './tyres.ts';
import { antiRoll, rideSetup, type RideSetup } from './ride.ts';

import type { WheelQueries } from './curbQueries.ts';
const group = (GROUP.VEHICLE << 16) | GROUP.STATIC_SOLID | GROUP.DYNAMIC;
/** Rapier's brake is an impulse per 1/60 s step: the slip-curve tyre takes it as that force (N). */
const BRAKE_STEP = 1 / 60;
/** Contact impulse, as a speed change of the whole vehicle (m/s), that counts as a collision. */
export const IMPACT_MIN_DELTA_V = 0.5;
/** A car nobody drives whose chassis stays within REST_MOVE m and REST_TURN (1 - |q dot q0|) of one pose for
 *  REST_STEPS solver steps is put to sleep. Rapier's own sleep test reads the velocity, which the tyre model's
 *  per-step suspension and tyre impulses hold at -0.06 m/s on a fleet car standing still: every world car woken once
 *  (the character within 6 m, a touch, a shared record restored) then ran the whole vehicle step at 60 Hz for the rest
 *  of the session (applyTyres, updateVehicle, strikeCurbs, trackSuspension: ~60 s of 330 s of the Windows laptop's
 *  physics worker, frame-mp win-prof-1). */
export const REST = { move: 0.002, turn: 2e-7, steps: 30 } as const;
/** Body damage: a step whose contact velocity change reaches this (m/s) starts or continues a crash; a crash ends after
 *  two quieter steps or `CRASH_STEPS` steps, and is applied as one hit (vehicle/damage.ts: its severity is the square
 *  of the whole velocity change, so a crash split over several solver steps must not count as several light ones). */
export const CRASH_STEP_DV = 1, CRASH_STEPS = 18;
/** Scrapes (Vehicle.trackScrape): contacts sliding faster than `minSpeed` (m/s) wear the paint `wearPerMetre` per metre
 *  of sliding within `radius` (m) of the point (a 10 m scrape along a wall goes through to bare metal), and leave dirt
 *  from the ground or grime from walls; a new wire at most every `commitSteps` solver steps. */
export const SCRAPE = { minSpeed: 1.5, radius: .3, wearPerMetre: .06, dirtPerMetre: .2, grimePerMetre: .01, commitSteps: 30 } as const;

export class Vehicle {
  lights: 0 | 1 | 2 = 0;
  horn = false;
  readonly body: Rapier.RigidBody;
  readonly controller: Rapier.DynamicRayCastVehicleController;
  readonly kind: VehicleKind;
  readonly tuning: VehicleTuning;
  /** Chassis box half extents (+X forward). */
  readonly halfExt: V3;
  private contacts = new Map<string, V3>();
  private strikeId = 0;
  private lastStrike: VehicleState['curbStrike'] = null;
  private steering = 0;
  /** Slewed keyboard steer input (tuning.keySteer), following the raw input while a stick or the autopilot steers. */
  private keySteer = 0;
  /** Brake pressure 0..1 of a slip-curve car (tuning.brakes), building towards the pedal's share of `brakeForce`. */
  private brakePressure = 0;
  private impactId = 0;
  private impact: VehicleState['impact'] = null;
  private impactCooldown = 0;
  private readonly drive: number[];
  /** Velocity once every wheel, drag and curb impulse of the tick is applied: what the world step then changes. */
  private beforeSolve: V3 | null = null;
  /** This step's input came from a driver (step(dt, input)). */
  private driven = false;
  /** The pose a resting undriven car has kept, and for how many steps (REST). */
  private rest: { p: V3; q: { x: number; y: number; z: number; w: number }; steps: number } | null = null;
  private bumps = new BumpDetector();
  private readonly longitudinal = new Float32Array(4);
  private readonly lateral = new Float32Array(4);
  /** Rear-steer state of a 4WS vehicle (rearSteer.ts); the mode persists like `lights`. */
  private rear: RearSteerState = rearSteerIdle();
  /** Scheduled long-travel corners (suspension.ts), the last two reported lengths and the contact of the last tick. */
  private readonly corners: Corner[];
  private readonly lengths: Float64Array;
  private readonly rates: Float64Array;
  private readonly loaded: boolean[];
  /** Per-wheel surface grip multiplier from the last contact (tuning.grip.surface). */
  private readonly surfaceGrip: number[];
  /** Per-wheel surface grip of a slip-curve tyre from the last contact (tuning.tyre.surfaces; null: tarmac). */
  private readonly surface: (import('./tyres.ts').SurfaceGrip | null)[];
  private throttleApplied = 0;
  /** 4WS axle layout: front and rear axle x and the track. */
  private readonly axles: { xf: number; xr: number; track: number } | null;
  /** Body damage of a damageable car (vehicle/damage.ts), owned here; null for vehicles without a damage cage. */
  readonly damage: DamageState | null;
  /** Whether this client decides every hit on the body. A parked car's local copy (every client simulates its own,
   *  until one owns the shared record) takes no damage from another player's car: that player's client measures the
   *  hit and shares the car with its damage (net/sharedVehicles.ts). `ownDamage` once this client drives or owns it. */
  private ownsDamage: boolean;
  private wire: { revision: number; text: string | undefined } = { revision: -1, text: undefined };
  private mech: { revision: number; value: Mechanics } = { revision: -1, value: INTACT };
  /** Wheel mounts (chassis frame): the tuning's, moved with the damage cage along and across the car and up by a
   *  damaged corner's sag (vehicle/mechanics.ts); `mounted` is the damage revision they follow. */
  private readonly mounts: { x: number; y: number; z: number }[];
  private mounted = -1;
  /** Damageable cars: chassis velocities before the solver, and the crash being summed (chassis frame). */
  private damageBefore: { v: V3; w: V3 } | null = null;
  private crash: { dv: V3; dw: V3; steps: number; quiet: number; remote: boolean } | null = null;
  /** Slip-curve tyres (vehicle/tyres.ts, tuning.tyre): the last step's per-wheel forces along the wheel heading and
   *  axle (N) for diagnostics; null for vehicles on Rapier's own tyre. */
  readonly tyreForces: { fx: Float32Array; fy: Float32Array } | null;
  private readonly tyreResults: TyreResult[] = [];
  /** Slip-curve tyre solver memory (vehicle/tyres.ts). */
  private readonly tyreState: TyreState;
  /** Springs, dampers and anti-roll bars from tuning.ride (vehicle/ride.ts), and the bars' last forces per wheel. */
  private readonly ride: RideSetup | null;
  private readonly bars: Float64Array;

  private R: RapierApi;
  private world: Rapier.World;
  private surfaces: WheelQueries;

  constructor(R: RapierApi, world: Rapier.World, pos: V3, yaw: number, surfaces: WheelQueries,
    spec: { kind?: VehicleKind; mass?: number; halfExt?: V3; parked?: boolean; tuning?: VehicleTuning;
      damage?: { box: import('./damage.ts').DamageBox } } = {},
    private readonly contactMaterial: (collider: Rapier.Collider, point: V3) => number = () => 0) {
    this.R = R; this.world = world; this.surfaces = surfaces;
    this.kind = spec.kind ?? 'car';
    const t = this.tuning = spec.tuning ?? VEHICLES[this.kind];
    this.body = world.createRigidBody(R.RigidBodyDesc.dynamic().setTranslation(pos.x, pos.y, pos.z)
      .setRotation({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) })
      .setCanSleep(true).setCcdEnabled(true).setAngularDamping(t.angularDamping ?? 1.2));
    const h = this.halfExt = spec.halfExt ?? t.halfExt, mass = spec.mass ?? t.mass;
    const chassis = R.ColliderDesc.cuboid(h.x, h.y, h.z).setFriction(0.5);
    // A lowered centre of mass keeps the narrow outrigger layout upright; inertia stays the box's own.
    if (t.comDrop) {
      chassis.setMassProperties(mass, { x: t.comAhead ?? 0, y: -t.comDrop, z: 0 }, {
        x: mass * (h.y * h.y + h.z * h.z) / 3, y: mass * (h.x * h.x + h.z * h.z) / 3, z: mass * (h.x * h.x + h.y * h.y) / 3,
      }, { x: 0, y: 0, z: 0, w: 1 });
    } else chassis.setMass(mass);
    world.createCollider(chassis
      .setCollisionGroups((GROUP.VEHICLE << 16) | GROUP.STATIC_SOLID | GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC | REMOTE_VEHICLE | 0xff00), this.body);
    const v = this.controller = world.createVehicleController(this.body);
    v.indexUpAxis = 1;
    // Rapier exposes this setter under the name setIndexForwardAxis.
    v.setIndexForwardAxis = 0;
    // A ride tuning sets each wheel's spring and dampers from its axle's frequency and damping ratios (ride.ts).
    const ride = this.ride = rideSetup(t, mass);
    this.bars = new Float64Array(t.wheels.length);
    const spring = (i: number) => ride?.wheels[i] ?? { stiffness: t.suspensionStiffness, compression: t.suspensionCompression,
      relaxation: t.suspensionRelaxation, rest: t.rest, travel: t.suspensionTravel };
    t.wheels.forEach((w, i) => v.addWheel(w, { x: 0, y: -1, z: 0 }, { x: 0, y: 0, z: 1 }, spring(i).rest, t.wheelRadius));
    for (let i = 0; i < t.wheels.length; i++) {
      const k = spring(i);
      v.setWheelSuspensionStiffness(i, k.stiffness);
      v.setWheelSuspensionCompression(i, k.compression);
      v.setWheelSuspensionRelaxation(i, k.relaxation);
      v.setWheelMaxSuspensionTravel(i, k.travel);
      v.setWheelMaxSuspensionForce(i, t.maxSuspensionForce);
      // A slip-curve tyre applies its own forces (applyTyres): Rapier's side impulse is switched off.
      v.setWheelSideFrictionStiffness(i, t.tyre ? 0 : 1.1);
    }
    this.corners = corners(t);
    // Long travel: the short end of Rapier's clamp is each corner's full bump.
    this.corners.forEach((c, i) => v.setWheelMaxSuspensionTravel(i, t.rest - (c.staticLength - c.bump)));
    this.lengths = new Float64Array(t.wheels.length).fill(t.rest);
    this.rates = new Float64Array(t.wheels.length);
    this.loaded = t.wheels.map(() => false);
    this.surfaceGrip = t.wheels.map(() => 1);
    this.surface = t.wheels.map(() => null);
    const fx = t.front.map(i => t.wheels[i].x), rx = t.wheels.filter((_, i) => !t.front.includes(i)).map(w => w.x);
    this.axles = t.rearSteer ? { xf: fx.reduce((a, b) => a + b, 0) / fx.length, xr: rx.reduce((a, b) => a + b, 0) / rx.length,
      track: Math.max(...t.front.map(i => t.wheels[i].z)) - Math.min(...t.front.map(i => t.wheels[i].z)) } : null;
    // Engine force per driven wheel: the front share on the driven front-axle wheels, the rest on the driven rear ones.
    const front = t.driven.filter(i => t.front.includes(i)).length, rear = t.driven.length - front;
    this.drive = t.wheels.map((_, i) => !t.driven.includes(i) ? 0
      : t.front.includes(i) ? t.frontTorque * t.driven.length / front : (1 - t.frontTorque) * t.driven.length / rear);
    if (spec.parked) this.body.sleep();
    this.damage = spec.damage ? new DamageState(damageLayout(spec.damage.box)) : null;
    this.ownsDamage = !spec.parked;
    this.mounts = t.wheels.map(w => ({ ...w }));
    this.tyreState = tyreState(t.wheels.length);
    this.tyreForces = t.tyre ? { fx: new Float32Array(t.wheels.length), fy: new Float32Array(t.wheels.length) } : null;
  }

  /** Follows a damage change: the wheel mounts move with the cage at each hub (fleet frame +Z forward, +X left; the
   *  chassis frame +X forward, +Z right) and up by the corner's sag, and a damaged corner's dampers soften. */
  private mountWheels() {
    const d = this.damage;
    if (!d || d.revision === this.mounted) return;
    this.mounted = d.revision;
    const t = this.tuning, v = this.controller, m = this.mechanics(), o: [number, number, number] = [0, 0, 0];
    for (let i = 0; i < t.wheels.length; i++) {
      const w = t.wheels[i], mount = this.mounts[i];
      cageOffset(d, -w.z, t.wheelRadius, w.x, o);
      mount.x = w.x + o[2]; mount.y = w.y + (m.sag[i] ?? 0); mount.z = w.z - o[0];
      v.setWheelChassisConnectionPointCs(i, mount);
      if (this.corners[i]) continue;
      const base = this.ride?.wheels[i];
      v.setWheelSuspensionCompression(i, (base?.compression ?? t.suspensionCompression) * (m.damping[i] ?? 1));
      v.setWheelSuspensionRelaxation(i, (base?.relaxation ?? t.suspensionRelaxation) * (m.damping[i] ?? 1));
    }
  }

  /** The chassis velocity before this step's solve (null outside a step): what a car ran into something with. */
  get velocityBeforeSolve(): V3 | null { return this.beforeSolve; }
  /** From now on another player's car damages this body too (this client drives or owns it). */
  ownDamage() { this.ownsDamage = true; }
  /** Whether the chassis touches another player's car proxy now (world.ts setRemotes, REMOTE_VEHICLE membership). */
  private touchesRemote(): boolean {
    let touching = false;
    for (let i = 0; i < this.body.numColliders() && !touching; i++) this.world.contactPairsWith(this.body.collider(i), other => {
      if (!touching && (other.collisionGroups() >>> 16) & REMOTE_VEHICLE) this.world.contactPair(this.body.collider(i), other, m => { if (m.numContacts() > 0) touching = true; });
    });
    return touching;
  }

  /** A scrape along a face as the contacts would paint it (developer tools). */
  damageScrape(scrape: DamageScrape) { if (this.damage) applyScrape(this.damage, scrape, SCRAPE); }

  /** Applies a body hit (fleet model frame, vehicle/damage.ts); returns the structural depth it added (m). */
  damageHit(hit: DamageHit): number {
    return this.damage ? applyHit(this.damage, hit) : 0;
  }

  /** Sums each solver step's contact velocity change (beyond gravity, chassis frame) into one crash and applies it
   *  when the contact ends. Rapier reports no contact impulses against trimesh walls (afterStep), so the chassis
   *  momentum is the measure here too; its angular change locates the contact on the box. In multiplayer a remote
   *  car's kinematic proxy is such a contact as well, so the server's contact impulse (world.ts vehicleImpulse) adds
   *  no second hit: each owner measures its own car. */
  private trackCrash(dt: number) {
    const before = this.damageBefore;
    this.damageBefore = null;
    if (!this.damage) return;
    if (!before || this.body.isSleeping()) { this.finishCrash(); return; }
    const b = this.body, v = b.linvel(), w = b.angvel(), g = this.world.gravity.y * dt;
    const change = { x: v.x - before.v.x, y: v.y - before.v.y - g, z: v.z - before.v.z };
    const q = b.rotation(), inverse = { x: -q.x, y: -q.y, z: -q.z, w: q.w };
    if (Math.hypot(change.x, change.y, change.z) >= CRASH_STEP_DV) {
      const dv = rotate(change, inverse), dw = rotate({ x: w.x - before.w.x, y: w.y - before.w.y, z: w.z - before.w.z }, inverse);
      const c = this.crash ??= { dv: { x: 0, y: 0, z: 0 }, dw: { x: 0, y: 0, z: 0 }, steps: 0, quiet: 0, remote: false };
      if (!this.ownsDamage && !c.remote) c.remote = this.touchesRemote();
      c.dv.x += dv.x; c.dv.y += dv.y; c.dv.z += dv.z; c.dw.x += dw.x; c.dw.y += dw.y; c.dw.z += dw.z;
      c.steps++; c.quiet = 0;
      if (c.steps >= CRASH_STEPS) this.finishCrash();
    } else if (this.crash && ++this.crash.quiet >= 2) this.finishCrash();
  }

  private finishCrash() {
    const c = this.crash;
    this.crash = null;
    // Another player's car hit this parked copy: that player's client decides the damage.
    if (!c || c.remote) return;
    // Contact line from the impulse J and its angular impulse L = I dw about the centre of mass: r = (J x L) / |J|^2
    // plus any point along J; the contact is where that line enters the chassis box against J.
    const m = this.body.mass(), J = { x: c.dv.x * m, y: c.dv.y * m, z: c.dv.z * m }, inertia = this.body.principalInertia();
    const L = { x: c.dw.x * inertia.x, y: c.dw.y * inertia.y, z: c.dw.z * inertia.z };
    const j2 = J.x * J.x + J.y * J.y + J.z * J.z, h = this.halfExt;
    let at: [number, number, number] | undefined;
    if (j2 > 0) {
      const t = this.tuning, com = { x: t.comAhead ?? 0, y: -(t.comDrop ?? 0), z: 0 };
      const r = { x: (J.y * L.z - J.z * L.y) / j2 + com.x, y: (J.z * L.x - J.x * L.z) / j2 + com.y, z: (J.x * L.y - J.y * L.x) / j2 + com.z };
      const n = Math.sqrt(j2), d = { x: J.x / n, y: J.y / n, z: J.z / n };
      // Slab entry: the largest entry parameter over the axes along -d, clamped back onto the box.
      let s = -Infinity;
      for (const k of ['x', 'y', 'z'] as const) if (Math.abs(d[k]) > 1e-6) s = Math.max(s, Math.min((-h[k] - r[k]) / d[k], (h[k] - r[k]) / d[k]));
      const p = Number.isFinite(s) ? { x: r.x + s * d.x, y: r.y + s * d.y, z: r.z + s * d.z } : r;
      const clamp = (v: number, k: 'x' | 'y' | 'z') => Math.max(-h[k], Math.min(h[k], v));
      // Chassis (+X forward, +Z right) to the fleet model frame (+Z forward, +X left), scaled to the model's box.
      const box = this.damage!.layout, fx = -clamp(p.z, 'z') / h.z * box.width / 2, fz = clamp(p.x, 'x') / h.x * box.length / 2;
      at = [fx, (box.min[1] + box.max[1]) / 2, fz];
    }
    // A hit clearly off the middle of a face (a corner, a pole beside the centre) has a footprint from its point to
    // the nearer corner; anything nearer the middle is a wall. A square wall hit a few degrees off still reads as a
    // wall: the first corner's yaw impulse puts its estimate up to about a third of the way out.
    const box = this.damage!.layout, dv: [number, number, number] = [-c.dv.z, c.dv.y, c.dv.x];
    const end = Math.abs(dv[2]) >= Math.abs(dv[0]);
    const lateral = at ? Math.abs(end ? at[0] : at[2]) : 0, half = end ? box.width / 2 : box.length / 2;
    const halfWidth = lateral > .5 * half ? Math.max(.15, half - lateral) : undefined;
    this.damageHit({ dv, ...(at ? { at } : {}), ...(halfWidth ? { halfWidth } : {}) });
  }

  /** Scrapes wear the paint where the body slides along something: every third step, each chassis contact point's
   *  sliding speed paints wear on the box face it lies on (vehicle/damage.ts stampSurface, `SCRAPE`); a contact with
   *  the ground under a car on its side or roof adds dirt. Committed (a new damage revision, so a new wire) at most
   *  every half second. */
  private trackScrape(dt: number) {
    const d = this.damage;
    if (!d || this.body.isSleeping()) { this.scrape.speed = 0; return; }
    if (++this.scrape.tick % 3) return;
    const b = this.body, lv = b.linvel(), w = b.angvel(), t = b.translation(), q = b.rotation(), inverse = { x: -q.x, y: -q.y, z: -q.z, w: q.w };
    let fastest = 0;
    if (Math.hypot(lv.x, lv.y, lv.z) > SCRAPE.minSpeed) {
      const h = this.halfExt, box = d.layout, step = 3 * dt;
      for (let i = 0; i < b.numColliders(); i++) {
        const own = b.collider(i);
        this.world.contactPairsWith(own, other => {
          if (other.isSensor() || (!this.ownsDamage && (other.collisionGroups() >>> 16) & REMOTE_VEHICLE)) return;
          const ov = other.parent()?.linvel() ?? { x: 0, y: 0, z: 0 };
          this.world.contactPair(own, other, manifold => {
            const n = manifold.normal();
            for (let k = 0; k < manifold.numSolverContacts(); k++) {
              const p = manifold.solverContactPoint(k);
              if (!p) continue;
              const r = { x: p.x - t.x, y: p.y - t.y, z: p.z - t.z };
              const v = { x: lv.x + w.y * r.z - w.z * r.y - ov.x, y: lv.y + w.z * r.x - w.x * r.z - ov.y, z: lv.z + w.x * r.y - w.y * r.x - ov.z };
              const vn = v.x * n.x + v.y * n.y + v.z * n.z, slide = Math.hypot(v.x - vn * n.x, v.y - vn * n.y, v.z - vn * n.z);
              if (slide < SCRAPE.minSpeed) continue;
              fastest = Math.max(fastest, slide);
              // The face of the box the point lies on (chassis frame +X forward, +Y up, +Z right); the underbody wears nothing.
              const c = rotate(r, inverse), ax = Math.abs(c.x) / h.x, ay = Math.abs(c.y) / h.y, az = Math.abs(c.z) / h.z;
              let face: SurfaceFace;
              if (ay >= ax && ay >= az) { if (c.y < 0) continue; face = 'top'; }
              else if (ax >= az) face = c.x > 0 ? 'front' : 'rear';
              else face = c.z > 0 ? 'negX' : 'posX';
              const at: [number, number, number] = [-c.z / h.z * box.width / 2, (box.min[1] + box.max[1]) / 2 + c.y / h.y * box.height / 2, c.x / h.x * box.length / 2];
              // The ground under a car lying on its side or roof (an upward contact normal) leaves dirt; walls a little grime.
              const ground = Math.abs(n.y) > .7, metres = slide * step;
              if (stampSurface(d, face, at, SCRAPE.radius, metres * SCRAPE.wearPerMetre, metres * (ground ? SCRAPE.dirtPerMetre : SCRAPE.grimePerMetre)))
                this.scrape.pending = true;
            }
          });
        });
      }
    }
    this.scrape.speed = Math.round(fastest * 10) / 10;
    if (this.scrape.pending && this.scrape.tick - this.scrape.committed >= SCRAPE.commitSteps) {
      this.scrape.pending = false; this.scrape.committed = this.scrape.tick;
      if (!d.hits) d.scraped = true;
      d.revision++;
    }
  }
  private scrape = { tick: 0, committed: -Infinity, pending: false, speed: 0 };

  /** The mechanical state of the car's damage (vehicle/mechanics.ts), recomputed only when a hit changed it. */
  mechanics(): Mechanics {
    const d = this.damage;
    if (!d) return INTACT;
    if (d.revision !== this.mech.revision) this.mech = { revision: d.revision, value: mechanics(d) };
    return this.mech.value;
  }

  /** Replaces the body damage with a saved or shared state (a malformed one is ignored), or a fresh body for null. */
  setDamage(state: string | null) {
    const d = this.damage;
    if (!d || (state ?? undefined) === this.damageWire()) return;
    if (state === null) { if (d.damaged) d.clear(); }
    else decodeDamage(state, d);
  }

  /** The damage state's wire form, re-encoded only when a hit changed it; undefined while undamaged. */
  damageWire(): string | undefined {
    const d = this.damage;
    if (!d || d.revision === this.wire.revision) return this.wire.text;
    this.wire = { revision: d.revision, text: d.damaged ? encodeDamage(d) : undefined };
    return this.wire.text;
  }

  recordImpact(impulse: number) {
    if (impulse / this.body.mass() >= IMPACT_MIN_DELTA_V) this.impact = { id: ++this.impactId, impulse };
  }

  reset(pos: V3, yaw: number) {
    this.body.setTranslation(pos, true);
    this.body.setRotation({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) }, true);
    this.body.setLinvel({ x: 0, y: 0, z: 0 }, true);
    this.body.setAngvel({ x: 0, y: 0, z: 0 }, true);
    this.contacts.clear(); this.lastStrike = null; this.steering = 0; this.keySteer = 0; this.brakePressure = 0; this.impact = null; this.impactCooldown = 0;
    this.crash = null; this.damageBefore = null;
    this.rear = rearSteerIdle(this.rear.mode);
    this.lengths.fill(this.tuning.rest); this.rates.fill(0); this.loaded.fill(false);
    this.beforeSolve = null;
    this.bumps.reset();
    this.longitudinal.fill(0); this.lateral.fill(0); resetTyres(this.tyreState);
  }

  step(dt: number, input: VehicleInput | null) {
    this.horn = input?.horn === true;
    if (input?.lights !== undefined) this.lights = input.lights;
    if (input?.rearMode !== undefined && REAR_STEER_MODES.includes(input.rearMode) && input.rearMode !== this.rear.mode) {
      this.rear = { ...this.rear, mode: input.rearMode };
    }
    this.beforeSolve = null;
    this.driven = input !== null;
    // A sleeping damageable car still measures a hit that wakes it during the coming solver step.
    this.damageBefore = this.damage && this.body.isSleeping() ? { v: { x: 0, y: 0, z: 0 }, w: { x: 0, y: 0, z: 0 } } : null;
    if (!input && this.body.isSleeping()) return;
    const v = this.controller, b = this.body;
    const f = rotate({ x: 1, y: 0, z: 0 }, b.rotation()), lv = b.linvel();
    const speed = lv.x * f.x + lv.y * f.y + lv.z * f.z;
    const t = this.tuning, h = handling(this.keyboard(input, dt), speed, t), m = this.mechanics();
    this.mountWheels();
    // A damaged radiator or engine gives less force at every speed (lower torque and lower power alike).
    h.engine *= m.power;
    this.steering += Math.max(-dt * t.steerRate, Math.min(dt * t.steerRate, h.steer - this.steering));
    this.throttleApplied = h.drive;
    const rs = t.rearSteer, axles = this.axles;
    if (rs) this.rear = rearSteerStep(rs, this.rear, this.steering / t.maxSteer, speed, dt);
    // CRAB keeps all four wheels parallel: the front stops at the rear's 32° lock instead of turning on to 42°.
    const front = rs && this.rear.mode === 'crab' ? Math.max(-rs.maxAngle, Math.min(rs.maxAngle, this.steering)) : this.steering;
    for (let i = 0; i < t.wheels.length; i++) {
      const isFront = t.front.includes(i), driven = t.driven.includes(i), at = t.wheels[i];
      v.setWheelSteering(i, (rs && axles
        ? (isFront || rs.wheels.includes(i) ? ackermann4(front, this.rear.angle, axles.xf, axles.xr, axles.track, at.x, at.z,
          isFront ? t.maxSteer : rs.maxAngle) : 0)
        : t.steered.includes(i) ? ackermann(this.steering,
          Math.max(...t.wheels.map(w => w.x)) - Math.min(...t.wheels.map(w => w.x)), at.z) : 0)
        // Damage: a bent steering turns the steered wheels, each damaged corner has its own toe (mechanics.ts).
        + (m.toe[i] ?? 0) + (t.steered.includes(i) ? m.steer : 0));
      if (t.tyre) continue;
      v.setWheelEngineForce(i, h.engine * this.drive[i]);
      // The handbrake locks the driven rear wheels only.
      v.setWheelBrake(i, h.brake + (driven && !isFront && h.handbrake ? t.brakeForce : 0));
      let grip = (isFront ? h.frontGrip : h.rearGrip) * this.surfaceGrip[i];
      if (t.frictionCircle && driven && !isFront && this.surfaceGrip[i] < 1 && v.wheelIsInContact(i)) {
        // Drive force against the tyre's μ·Fz (last tick's load). Rapier's ray-cast tyre keeps full side grip under
        // drive, so past the knee (`use` > 1, the drive alone saturating the tyre) side grip falls to the floor.
        const fz = v.wheelSuspensionForce(i) ?? 0, use = fz > 1 ? Math.abs(h.engine * this.drive[i]) / (grip * fz) : 0;
        const fc = t.frictionCircle, u = Math.min(1, Math.max(0, (use - fc.knee) / fc.span));
        grip *= 1 - (1 - fc.floor) * u * u * (3 - 2 * u);
      }
      v.setWheelFrictionSlip(i, grip);
    }
    this.scheduleSuspension(dt);
    // Contact velocity includes yaw: rear wheels slide differently from the chassis centre.
    const q = b.rotation(), angular = b.angvel();
    for (let i = 0; i < t.wheels.length; i++) {
      const offset = rotate(this.mounts[i], q), steer = v.wheelSteering(i) ?? 0;
      const forward = rotate({ x: Math.cos(steer), y: 0, z: -Math.sin(steer) }, q);
      const side = rotate({ x: Math.sin(steer), y: 0, z: Math.cos(steer) }, q);
      const vx = lv.x + angular.y * offset.z - angular.z * offset.y;
      const vy = lv.y + angular.z * offset.x - angular.x * offset.z;
      const vz = lv.z + angular.x * offset.y - angular.y * offset.x;
      this.lateral[i] = vx * side.x + vy * side.y + vz * side.z;
      this.longitudinal[i] = vx * forward.x + vy * forward.y + vz * forward.z;
    }
    v.updateVehicle(dt, this.R.QueryFilterFlags.EXCLUDE_SENSORS, group);
    this.trackSuspension(dt);
    this.applyBars(dt, input !== null);
    if (t.tyre) this.applyTyres(dt, h, input !== null);
    else if (t.rollTransfer) {
      const up = rotate({ x: 0, y: 1, z: 0 }, q), com = b.worldCom();
      let contacts = 0;
      for (let i = 0; i < t.wheels.length; i++) if (v.wheelIsInContact(i)) contacts++;
      const torque = { x: 0, y: 0, z: 0 };
      for (let i = 0; i < t.wheels.length; i++) {
        if (!v.wheelIsInContact(i)) continue;
        const normal = v.wheelContactNormal(i), point = v.wheelContactPoint(i);
        if (!normal || !point) continue;
        const steer = v.wheelSteering(i) ?? 0;
        const axle = rotate({ x: Math.sin(steer), y: 0, z: Math.cos(steer) }, q);
        const alongNormal = dot(axle, normal);
        axle.x -= normal.x * alongNormal; axle.y -= normal.y * alongNormal; axle.z -= normal.z * alongNormal;
        const length = Math.hypot(axle.x, axle.y, axle.z);
        if (length < 0.001) continue;
        axle.x /= length; axle.y /= length; axle.z /= length;
        const height = dot({ x: com.x - point.x, y: com.y - point.y, z: com.z - point.z }, up);
        // Only Rapier's side impulse loses the contact lever arm. Engine, brake
        // and suspension impulses already act at their full contact point.
        const roll = tyreRollImpulse(b.mass(), (v.wheelSideImpulse(i) ?? 0) / b.mass(), height, dt, t.rollTransfer, contacts, up.y);
        torque.x += (up.y * axle.z - up.z * axle.y) * roll;
        torque.y += (up.z * axle.x - up.x * axle.z) * roll;
        torque.z += (up.x * axle.y - up.y * axle.x) * roll;
      }
      b.applyTorqueImpulse(torque, true);
    }
    if (t.airPitch && t.wheels.every((_, i) => !v.wheelIsInContact(i))) {
      // In the air: the pitch rate about the chassis Z axis decays (aero and wheel inertia) and the throttle/brake
      // pitch the chassis, so a kicker launch does not nose over and the driver can level a landing.
      const z = rotate({ x: 0, y: 0, z: 1 }, q), w = b.angvel(), rate = w.x * z.x + w.y * z.y + w.z * z.z;
      const accel = t.airPitch.control * ((input?.throttle ?? 0) - (input?.brake ?? 0));
      const change = -rate * (1 - Math.exp(-t.airPitch.damping * dt)) + accel * dt;
      b.setAngvel({ x: w.x + z.x * change, y: w.y + z.y * change, z: w.z + z.z * change }, true);
    }
    for (let i = 0; i < t.wheels.length; i++) {
      if (t.tyre) this.longitudinal[i] = this.tyreResults[i]?.slide ?? 0;
      else this.longitudinal[i] = longitudinalSlip(this.longitudinal[i],
        Math.abs(h.engine * this.drive[i]) * dt + (v.wheelBrake(i) ?? 0),
        (v.wheelSuspensionForce(i) ?? 0) * dt * (v.wheelFrictionSlip(i) ?? 0));
      if (!v.wheelIsInContact(i)) this.lateral[i] = this.longitudinal[i] = 0;
    }
    // Quadratic aerodynamic drag gives a soft 25 m/s terminal speed, including downhill coasting.
    const vel = b.linvel(), horizontal = Math.hypot(vel.x, vel.z);
    const drag = Math.min(0.15, (0.015 + horizontal * 0.004 * (t.drag ?? 1)) * dt);
    b.applyImpulse({ x: -vel.x * b.mass() * drag, y: 0, z: -vel.z * b.mass() * drag }, true);
    this.strikeCurbs(dt, input);
    this.detectBump(dt);
    this.beforeSolve = { ...b.linvel() };
    if (this.damage) this.damageBefore = { v: { ...this.beforeSolve }, w: { ...b.angvel() } };
  }

  /** Slip-curve tyres (vehicle/tyres.ts): after the raycast has applied the suspension, solves and applies the tyre
   *  forces from each wheel's slip angle, load, drive and brake. The damage layer's toe and steering pull are already
   *  in Rapier's wheel steering, its sag and damping in the suspension, its power loss in `h.engine`. */
  private applyTyres(dt: number, h: ReturnType<typeof handling>, driven: boolean) {
    const t = this.tuning, v = this.controller, b = this.body, q = b.rotation();
    // Stability control (tuning.stability): when the rear axle slides more than the front (oversteer), it cuts the
    // drive and brakes the front wheel on the outside of the yaw, by the excess of the rear's relaxed slip angle.
    const esc = this.stability(), brake = this.brakeForces(h.brake, dt);
    // Drive force per wheel: each driven axle's total, split evenly by an open differential, by load by a locked one.
    const drive = t.wheels.map((_, i) => h.engine * this.drive[i] * (1 - esc.cut)), lock = t.differential ?? 0;
    if (lock > 0) for (const axle of [t.front, t.wheels.map((_, i) => i).filter(i => !t.front.includes(i))]) {
      const wheels = axle.filter(i => this.drive[i] !== 0 && v.wheelIsInContact(i));
      const total = wheels.reduce((a, i) => a + drive[i], 0), load = wheels.reduce((a, i) => a + (v.wheelSuspensionForce(i) ?? 0), 0);
      if (wheels.length < 2 || load <= 0) continue;
      for (const i of wheels) drive[i] = total * ((1 - lock) / wheels.length + lock * (v.wheelSuspensionForce(i) ?? 0) / load);
    }
    const contacts = t.wheels.map((_, i): TyrePatch | null => {
      const point = v.wheelIsInContact(i) ? v.wheelContactPoint(i) : null, normal = point ? v.wheelContactNormal(i) : null;
      if (!point || !normal) return null;
      const steer = v.wheelSteering(i) ?? 0, axle = rotate({ x: Math.sin(steer), y: 0, z: Math.cos(steer) }, q);
      const along = dot(axle, normal);
      const s = { x: axle.x - normal.x * along, y: axle.y - normal.y * along, z: axle.z - normal.z * along };
      const length = Math.hypot(s.x, s.y, s.z);
      if (length < 1e-3) return null;
      s.x /= length; s.y /= length; s.z /= length;
      const forward = { x: normal.y * s.z - normal.z * s.y, y: normal.z * s.x - normal.x * s.z, z: normal.x * s.y - normal.y * s.x };
      const front = t.front.includes(i);
      return { point, normal, forward, side: s, load: (v.wheelSuspensionForce(i) ?? 0) + this.bars[i], axle: front ? 0 : 1,
        drive: drive[i], brake: brake[i] + (i === esc.wheel ? esc.brake : 0), locked: !front && h.handbrake, surface: this.surface[i] };
    });
    // Read the matrix at once: its getters view a scratch buffer that the next Rapier query (worldCom) overwrites.
    const m = b.effectiveWorldInvInertia(), invInertia = [m.m11, m.m12, m.m13, m.m12, m.m22, m.m23, m.m13, m.m23, m.m33];
    const body = { lin: { ...b.linvel() }, ang: { ...b.angvel() }, com: { ...b.worldCom() }, invMass: 1 / b.mass(), invInertia,
      gravity: { ...this.world.gravity } };
    const corner = b.mass() * Math.abs(this.world.gravity.y) / t.wheels.length;
    const r = solveTyres(body, contacts, t.tyre!, corner, dt, this.tyreResults, this.tyreState);
    // Like Rapier's own tyre impulses, these wake only a driven car: a parked one holding its brakes may fall asleep.
    b.applyImpulse(r.impulse, driven);
    b.applyTorqueImpulse(r.angular, driven);
    for (let i = 0; i < t.wheels.length; i++) { this.tyreForces!.fx[i] = this.tyreResults[i].fx; this.tyreForces!.fy[i] = this.tyreResults[i].fy; }
  }

  /** Brake force per wheel (N) of a slip-curve car: Rapier's per-step brake impulse taken as that force, and with
   *  tuning.brakes a pressure that builds over `ramp` seconds and a front/rear balance of the same total. */
  private brakeForces(brake: number, dt: number): number[] {
    const t = this.tuning, b = t.brakes, n = t.wheels.length;
    if (!b) return t.wheels.map(() => brake / BRAKE_STEP);
    const want = t.brakeForce > 0 ? brake / t.brakeForce : 0, step = dt / Math.max(dt, b.ramp);
    this.brakePressure += Math.max(-step, Math.min(step, want - this.brakePressure));
    const total = this.brakePressure * t.brakeForce / BRAKE_STEP * n, front = t.front.length;
    return t.wheels.map((_, i) => t.front.includes(i) ? total * b.front / front : total * (1 - b.front) / (n - front));
  }

  /** Stability control of a slip-curve car from the last step's relaxed slip angles (vehicle/tyres.ts): its share
   *  0..`max` rises from `onset` to `full` degrees of rear slip beyond the front's; `cut` scales the drive and `brake`
   *  (N) goes to the front wheel on the outside of the yaw. */
  private stability(): { cut: number; brake: number; wheel: number } {
    const s = this.tuning.stability, t = this.tuning, lag = this.tyreState.lag, v = this.controller;
    if (!s) return { cut: 0, brake: 0, wheel: -1 };
    const mean = (wheels: number[]) => { const on = wheels.filter(i => v.wheelIsInContact(i));
      return on.length ? on.reduce((a, i) => a + Math.abs(lag[i]), 0) / on.length : 0; };
    const rear = t.wheels.map((_, i) => i).filter(i => !t.front.includes(i));
    const excess = (mean(rear) - mean([...t.front])) * 180 / Math.PI;
    // The whole car sliding tail-out (all four tyres past their peak, as on a loose surface under power): the body's
    // sideslip against its yaw, from `sideslip` to twice it.
    const b = this.body, q = b.rotation(), lv = b.linvel(), f = rotate({ x: 1, y: 0, z: 0 }, q), l = rotate({ x: 0, y: 0, z: -1 }, q);
    const along = lv.x * f.x + lv.y * f.y + lv.z * f.z, across = lv.x * l.x + lv.y * l.y + lv.z * l.z, yaw = b.angvel().y;
    const beta = Math.abs(along) > 3 && across * yaw < 0 ? Math.abs(Math.atan2(across, Math.abs(along))) * 180 / Math.PI : 0;
    const u = s.max * Math.min(1, Math.max(0, (excess - s.onset) / (s.full - s.onset), (beta - s.sideslip) / s.sideslip));
    if (u <= 0) return { cut: 0, brake: 0, wheel: -1 };
    // Yawing left (+y), the outside is the right (+z) front wheel.
    const left = this.body.angvel().y > 0, wheel = [...t.front].sort((p, q) => t.wheels[p].z - t.wheels[q].z)[left ? t.front.length - 1 : 0];
    return { cut: u, brake: u * this.body.mass() * Math.abs(this.world.gravity.y) / t.wheels.length, wheel };
  }

  /** Anti-roll bars (tuning.ride, ride.ts): a force pair at each axle's wheel mounts against the difference of their
   *  compressions, which also moves that much load between the axle's tyres (the tyre layer adds `bars`). */
  private applyBars(dt: number, driven: boolean) {
    const ride = this.ride, v = this.controller;
    if (!ride) return;
    antiRoll(ride, i => v.wheelIsInContact(i) ? ride.wheels[i].rest - (v.wheelSuspensionLength(i) ?? ride.wheels[i].rest) : null,
      i => v.wheelSuspensionForce(i) ?? 0, this.bars);
    const b = this.body, q = b.rotation(), p = b.translation(), up = rotate({ x: 0, y: 1, z: 0 }, q);
    for (let i = 0; i < this.bars.length; i++) {
      const f = this.bars[i] * dt;
      if (!f) continue;
      const at = rotate(this.mounts[i], q);
      b.applyImpulseAtPoint({ x: up.x * f, y: up.y * f, z: up.z * f }, { x: p.x + at.x, y: p.y + at.y, z: p.z + at.z }, driven);
    }
  }

  /** Keyboard steering (tuning.keySteer, Wreckfest 2: a held key turns the road wheels at about 46°/s): the digital
   *  ±1 of a key becomes a ramp towards it, faster back towards centre. Analog input passes through. */
  private keyboard(input: VehicleInput | null, dt: number): VehicleInput | null {
    const k = this.tuning.keySteer;
    if (!input || !k || !input.steerKey) { this.keySteer = input?.steer ?? 0; return input; }
    const target = Math.max(-1, Math.min(1, input.steer)), now = this.keySteer;
    const rate = target * now < 0 || Math.abs(target) < Math.abs(now) ? k.centre : k.rate;
    this.keySteer = now + Math.max(-rate * dt, Math.min(rate * dt, target - now));
    return { ...input, steer: this.keySteer };
  }

  /** Long travel (suspension.ts): sets each corner's Rapier spring and dampers for the coming raycast. */
  private scheduleSuspension(dt: number) {
    const v = this.controller, mass = this.body.mass();
    this.corners.forEach((c, i) => {
      const k = schedule(c, this.lengths[i], this.loaded[i] ? this.rates[i] : 0, this.tuning.rest, mass, dt, 0.95 * this.tuning.maxSuspensionForce);
      v.setWheelSuspensionStiffness(i, k.stiffness);
      v.setWheelSuspensionCompression(i, k.compression);
      v.setWheelSuspensionRelaxation(i, k.relaxation);
    });
  }

  /** After the raycast: suspension lengths and compression speed, load, and the surface grip of the contacts. */
  private trackSuspension(dt: number) {
    const v = this.controller, t = this.tuning, grip = t.grip?.surface, surfaces = t.tyre?.surfaces;
    for (let i = 0; i < t.wheels.length; i++) {
      const length = v.wheelSuspensionLength(i) ?? t.rest, contact = v.wheelIsInContact(i);
      const loaded = contact && (!this.corners[i] || length <= droopLength(t, i) + 1e-3);
      this.rates[i] = loaded && this.loaded[i] ? (this.lengths[i] - length) / dt : 0;
      this.lengths[i] = length; this.loaded[i] = loaded;
      if (!grip && !surfaces) continue;
      const collider = contact ? v.wheelGroundObject(i) : null, point = collider ? v.wheelContactPoint(i) : null;
      if (!collider || !point) continue;
      const surface = MATERIALS[this.contactMaterial(collider, point)]?.audio ?? '';
      if (surfaces) this.surface[i] = surfaces[surface] ?? null;
      const pair = grip?.[surface];
      this.surfaceGrip[i] = pair ? pair[t.front.includes(i) ? 0 : 1] : 1;
    }
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
      const wheel = this.mounts[i];
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
    this.trackCrash(dt);
    this.trackScrape(dt);
    this.settle();
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

  /** Sleeps an undriven car that has kept one pose for REST.steps steps (REST). */
  private settle() {
    const b = this.body;
    if (this.driven || b.isSleeping()) { this.rest = null; return; }
    const p = b.translation(), q = b.rotation(), r = this.rest;
    if (r && Math.abs(p.x - r.p.x) < REST.move && Math.abs(p.y - r.p.y) < REST.move && Math.abs(p.z - r.p.z) < REST.move
      && 1 - Math.abs(q.x * r.q.x + q.y * r.q.y + q.z * r.q.z + q.w * r.q.w) < REST.turn) {
      if (++r.steps >= REST.steps) { b.sleep(); this.rest = null; }
      return;
    }
    this.rest = { p: { x: p.x, y: p.y, z: p.z }, q: { x: q.x, y: q.y, z: q.z, w: q.w }, steps: 0 };
  }

  state(): VehicleState {
    const b = this.body, v = this.controller, lv = b.linvel();
    const forward = rotate({ x: 1, y: 0, z: 0 }, b.rotation());
    const t = this.tuning, rear = t.rearSteer ? { rearMode: this.rear.mode, rearLocked: this.rear.locked } : {};
    const damage = this.damageWire();
    return { ...rear, ...(damage ? { damage } : {}), ...(this.scrape.speed ? { scrape: this.scrape.speed } : {}), throttleApplied: this.throttleApplied, lights: this.lights, horn: this.horn, kind: this.kind, pos: { ...b.translation() }, rot: { ...b.rotation() }, linvel: { ...lv }, speed: Math.hypot(lv.x, lv.y, lv.z),
      forwardSpeed: lv.x * forward.x + lv.y * forward.y + lv.z * forward.z,
      curbStrike: this.lastStrike, impact: this.impact, bump: this.bumps.latest,
      wheels: this.mounts.map((cp, i) => {
        // A long-travel wheel hangs from its limit strap: past full droop it is unloaded and drawn at the strap.
        const raw = v.wheelSuspensionLength(i) ?? t.rest, curve = !!this.corners[i];
        const suspension = curve ? Math.min(raw, droopLength(t, i)) : raw;
        const contact = v.wheelIsInContact(i) && (!curve || raw <= droopLength(t, i) + 1e-3);
        const point = contact && !b.isSleeping() ? v.wheelContactPoint(i) : null;
        const normal = point ? v.wheelContactNormal(i) : null, collider = point ? v.wheelGroundObject(i) : null;
        const q = b.rotation(), inverse = { x: -q.x, y: -q.y, z: -q.z, w: q.w }, p = b.translation();
        const tyre = point && normal && collider ? {
          point: rotate({ x: point.x - p.x, y: point.y - p.y, z: point.z - p.z }, inverse),
          normal: rotate(normal, inverse), material: this.contactMaterial(collider, point),
          lateral: this.lateral[i], longitudinal: this.longitudinal[i],
        } : undefined;
        return { pos: { x: cp.x, y: cp.y - suspension, z: cp.z }, steer: v.wheelSteering(i) ?? 0,
          tyre, rotation: v.wheelRotation(i) ?? 0, contact, suspension };
      }) };
  }
}
