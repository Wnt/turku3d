import * as THREE from 'three/webgpu';
import { FLEET_BY_ID, fleetModel } from '../fleet/catalog';
import { AUTHORED_BY_ID } from '../fleet/registry';
import { CAR } from '../../physics/constants';
import { modelTuning } from '../../vehicle/modelTuning';
import { fleetLayout, steerFraction, steeringAngle, type DriverLayout } from './layout';
import { AmbientDrivers, type AmbientSeat } from './ambient';
import { BATCH_SLOTS, DriverBatch } from './batch';
import { bakeAtlas } from './model';
import { DriverMotion, type Vec } from './motion';
import { DriverRig, DriverTemplate } from './rig';

/** Seated, animated drivers for every driven car and truck: the local player's and every remote player's
 *  (game/src/actors/driver/README.md). Each frame the code that draws a driven vehicle calls `seat()` with the
 *  Object3D in the vehicle's fleet model frame (+Z forward, +X left, tyres on Y = 0) and what it draws of the
 *  vehicle; `finish()` then animates those within FULL_DISTANCE of the camera and hides the rest. All of them are one
 *  instanced draw (batch.ts): each rig poses its skeleton in its seat frame, off the scene graph. */

/** Drivers animate fully within this distance (m) of the camera, with 5 m of hysteresis; beyond, none is drawn. */
export const FULL_DISTANCE = 60;
const HYSTERESIS = 5;
/** Detail tiers (m from the camera; model.ts TIERS): eyes, brows and lashes; the legs (under the dash); the drawn
 *  steering wheel; beyond POSE_EVERY_OTHER a driver is posed on alternate frames (it holds its pose between). The batch
 *  collapses a driver's tiers beyond its distance. */
const FACE_DISTANCE = 8, LEG_DISTANCE = 15, WHEEL_DISTANCE = 30, POSE_EVERY_OTHER = 30;

export interface DriverInput {
  /** World velocity of the drawn vehicle (m/s). */
  velocity: Vec;
  /** The drawn wheel states (front pair first): their steer turns the steering wheel and the head. */
  wheels?: readonly { steer: number }[];
  /** Physics impact event id (locally simulated vehicles). */
  impact?: number;
  /** The horn sounds (vehicle signals; remote poses carry it): the right hand presses the hub. */
  horn?: boolean;
}

interface Driver {
  /** `frame`: the (empty) seat in the vehicle's frame; `local`: the rig's own seat frame, off the scene graph. */
  key: string; frame: THREE.Group; local: THREE.Group; rig: DriverRig; motion: DriverMotion; index: number; layout: DriverLayout | null;
  seatedFrame: number; input: DriverInput | null; drawn: boolean;
  /** Its seat-frame bone matrices (batch.ts) and whether the batch still holds another driver's in its layer. */
  bones: Float32Array; fresh: boolean;
}

const layouts = new Map<string, DriverLayout>();
/** The layout for fleet model `id` (an exact id, or a record family resolved like the drawn model), cached. */
export function fleetDriverLayout(id: string, seed = id): DriverLayout {
  const model = FLEET_BY_ID.get(id) ?? fleetModel(id, seed);
  let layout = layouts.get(model.id);
  if (!layout) {
    const pivots = AUTHORED_BY_ID.get(model.id)?.wheels.pivots;
    const frontAxle = pivots ? Math.max(pivots.wheel_FL[2], pivots.wheel_FR[2]) : model.wheelbase / 2;
    layout = fleetLayout({ ...model, frontAxle, steerLock: modelTuning(model.id)?.maxSteer ?? CAR.maxSteer });
    layouts.set(model.id, layout);
  }
  return layout;
}

export class Drivers {
  private template: DriverTemplate | null = null;
  private readonly drivers = new Map<string, Driver>();
  private readonly free: Driver[] = [];
  private frameId = 0;
  private created = 0;
  private readonly camera = new THREE.Vector3();
  private readonly at = new THREE.Vector3();
  private readonly basis = { fwd: { x: 0, y: 0, z: 1 }, left: { x: 1, y: 0, z: 0 }, up: { x: 0, y: 1, z: 0 } };
  private batch: DriverBatch | null = null;
  private readonly frustum = new THREE.Frustum();
  private readonly view = new THREE.Matrix4();
  private readonly sphere = new THREE.Sphere(new THREE.Vector3(), 1.2);
  private ambientDrivers: AmbientDrivers | null = null;
  private readonly ambientSeats: AmbientSeat[] = [];
  /** Main-thread ms spent in finish() (smoothed), for the frame-cost measurement. */
  frameMs = 0;

  /** `prepare` builds the driver's programs ahead (world.prepareGraphics). */
  constructor(private readonly prepare: (model: THREE.Object3D) => Promise<void> = async () => {}) {}

  /** The loaded player character (actors/player.ts `template`) every driver is cloned from; `renderer` bakes the
   *  drivers' colour atlas from its maps (model.ts; without one, as in Node tests, drivers are untextured); the drivers'
   *  instanced draw goes under `parent` (the scene). */
  setCharacter(scene: THREE.Object3D, clips: readonly THREE.AnimationClip[], renderer?: THREE.WebGPURenderer, parent?: THREE.Object3D) {
    const template = this.template = new DriverTemplate(scene, clips);
    if (renderer) template.model.material.map = bakeAtlas(renderer, template.model.sources);
    const batch = this.batch = new DriverBatch(template.model, template.model.boneCount);
    // Empty until a driver sits (count 0): preparation still builds its programs.
    batch.mesh.userData.prepareHidden = true;
    parent?.add(batch.mesh);
    if (!parent) batch.mesh.userData.preparesDetached = true;
    void this.prepare(batch.mesh).catch(error => console.warn(`[drivers] driver preparation failed: ${String(error)}`));
  }

  /** The drivers' instanced draw (null before the character loads). */
  get mesh(): THREE.InstancedMesh | null { return this.batch?.mesh ?? null; }

  /** Seats the driver `key` this frame in `parent` (the vehicle's fleet-frame Object3D) with `layout`. */
  seat(key: string, parent: THREE.Object3D, layout: DriverLayout, input: DriverInput) {
    if (!this.template) return;
    let driver = this.drivers.get(key);
    // One seat per driver and frame: the first caller (the visual that draws the vehicle) keeps it.
    if (driver?.seatedFrame === this.frameId) return;
    if (!driver) {
      driver = this.free.pop() ?? this.create();
      driver.key = key; driver.motion.reset(); driver.drawn = false; driver.fresh = true;
      this.drivers.set(key, driver);
    }
    if (driver.frame.parent !== parent) { parent.add(driver.frame); driver.motion.reset(); driver.fresh = true; }
    driver.layout = layout;
    driver.seatedFrame = this.frameId; driver.input = input;
  }

  private create(): Driver {
    const frame = new THREE.Group(); frame.name = 'driver-seat';
    const rig = new DriverRig(this.template!), local = new THREE.Group();
    local.add(rig.model);
    return { key: '', frame, local, rig, motion: new DriverMotion(), index: this.created++, layout: null, seatedFrame: -1, input: null,
      drawn: false, bones: new Float32Array(this.batch!.stride), fresh: true };
  }

  /** Animates the drivers seated this frame near `camera`, hides the others. Call after every seat(), before render. */
  finish(camera: THREE.Camera, now = performance.now()) {
    const t0 = performance.now(), batch = this.batch;
    camera.updateMatrixWorld();
    this.camera.setFromMatrixPosition(camera.matrixWorld);
    this.frustum.setFromProjectionMatrix(this.view.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse), camera.coordinateSystem);
    batch?.begin();
    for (const [key, driver] of this.drivers) {
      if (driver.seatedFrame !== this.frameId) {
        driver.frame.removeFromParent(); this.drivers.delete(key); this.free.push(driver);
        continue;
      }
      const input = driver.input!, layout = driver.layout!;
      const frame = driver.frame;
      frame.parent!.updateWorldMatrix(true, false);
      this.at.setFromMatrixPosition(frame.parent!.matrixWorld);
      const distance = this.at.distanceTo(this.camera);
      const near = distance < FULL_DISTANCE + (driver.drawn ? HYSTERESIS : 0) && driver.index < BATCH_SLOTS;
      frame.visible = near;
      if (!near) { driver.drawn = false; driver.motion.reset(); continue; }
      driver.drawn = true;
      frame.updateWorldMatrix(false, false);
      const e = frame.parent!.matrixWorld.elements;
      const unit = (x: number, y: number, z: number, out: Vec) => { const l = Math.hypot(x, y, z) || 1; out.x = x / l; out.y = y / l; out.z = z / l; return out; };
      const { fwd, left, up } = this.basis;
      unit(e[8], e[9], e[10], fwd); unit(e[0], e[1], e[2], left); unit(e[4], e[5], e[6], up);
      const steer = steerFraction(layout, input.wheels);
      const pose = driver.motion.update({ t: now / 1000, v: input.velocity, fwd, left, up, steer, impact: input.impact, horn: input.horn });
      if (driver.fresh || distance <= POSE_EVERY_OTHER || (this.frameId + driver.index) % 2 === 0) {
        // A vehicle with its own wheel node (the trophy truck) turns it by the same function (trophy4400/rig.ts).
        driver.rig.pose(driver.local, layout, pose, steeringAngle(layout.wheel, steer));
        driver.rig.writeBones(driver.bones);
        batch?.pose(driver.index, driver.bones, driver.fresh);
        driver.fresh = false;
      }
      // Culled one by one: a sphere over the seated body and its reach.
      this.sphere.center.set(layout.hips[0], layout.hips[1] + 0.25, layout.hips[2] + 0.2).applyMatrix4(frame.matrixWorld);
      if (!this.frustum.intersectsSphere(this.sphere)) continue;
      batch?.add(driver.index, frame.matrixWorld, distance < FACE_DISTANCE ? 3 : distance < LEG_DISTANCE ? 2 : distance < WHEEL_DISTANCE ? 1 : 0);
    }
    batch?.end();
    this.frameId++;
    this.frameMs += (performance.now() - t0 - this.frameMs) * 0.1;
  }

  /** Static drivers in this frame's ambient traffic cars near the camera (fleet/visuals.ts `trafficSeats`: model id
   *  and model-frame matrix), drawn under `parent` (ambient.ts). */
  ambient(parent: THREE.Object3D, seats: readonly { model: string; matrix: THREE.Matrix4 }[], count: number) {
    if (!this.template) return;
    for (let i = 0; i < count; i++) {
      const seat = this.ambientSeats[i] ??= { layout: null as unknown as DriverLayout, matrix: new THREE.Matrix4() };
      seat.layout = fleetDriverLayout(seats[i].model); seat.matrix.copy(seats[i].matrix);
    }
    (this.ambientDrivers ??= new AmbientDrivers(this.template, parent, this.prepare)).update(this.ambientSeats, count);
  }

  /** World position of driver `key`'s hips while it is seated and drawn (the seated-vehicle snapshot). */
  hipsOf(key: string): number[] | null {
    const d = this.drivers.get(key);
    return d?.drawn && d.frame.visible ? this.worldOf(d, 'DEF-hips') ?? null : null;
  }

  /** A bone's world position: the rig poses in its own seat frame, the seat follows the vehicle. */
  private worldOf(d: Driver, bone: string): number[] | undefined {
    return d.rig.model.getObjectByName(bone)?.getWorldPosition(new THREE.Vector3()).applyMatrix4(d.frame.matrixWorld).toArray();
  }

  /** Dev and test view: each seated driver, whether drawn, its hands' distance from the rim grips and motion. */
  snapshot() {
    return { frameMs: this.frameMs, pool: this.free.length, ambient: this.ambientDrivers?.snapshot() ?? {}, drivers: [...this.drivers.values()].map(d => ({
      key: d.key, drawn: d.drawn && d.frame.visible, cls: d.layout?.cls, reach: { ...d.rig.reach },
      hips: this.worldOf(d, 'DEF-hips'), head: this.worldOf(d, 'DEF-head'),
      pose: { ...d.motion.pose } })) };
  }
}
