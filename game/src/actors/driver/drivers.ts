import * as THREE from 'three/webgpu';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { FLEET_BY_ID, fleetModel } from '../fleet/catalog';
import { AUTHORED_BY_ID } from '../fleet/registry';
import { CAR } from '../../physics/constants';
import { modelTuning } from '../../vehicle/modelTuning';
import { fleetLayout, steerFraction, steeringAngle, type DriverLayout } from './layout';
import { DriverMotion, type Vec } from './motion';
import { DriverRig, DriverTemplate } from './rig';

/** Seated, animated drivers for every driven car and truck: the local player's and every remote player's
 *  (game/src/actors/driver/README.md). Each frame the code that draws a driven vehicle calls `seat()` with the
 *  Object3D in the vehicle's fleet model frame (+Z forward, +X left, tyres on Y = 0) and what it draws of the
 *  vehicle; `finish()` then animates those within FULL_DISTANCE of the camera and hides the rest. */

/** Drivers animate fully within this distance (m) of the camera, with 5 m of hysteresis; beyond, none is drawn. */
export const FULL_DISTANCE = 60;
const HYSTERESIS = 5;
/** Detail tiers (m from the camera): eyes, brows and lashes; the legs (under the dash); the drawn steering wheel; beyond
 *  POSE_EVERY_OTHER a driver is posed on alternate frames (it holds its pose between). Each skinned mesh is one draw. */
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
  key: string; frame: THREE.Group; rig: DriverRig; motion: DriverMotion; index: number;
  wheel: THREE.Group | null; spin: THREE.Object3D | null; layout: DriverLayout | null;
  seatedFrame: number; input: DriverInput | null; drawn: boolean;
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

/** A plain steering wheel: rim, three spokes and a hub in the XY plane, facing +Z, radius 1 (scaled per layout). */
function wheelGeometry(): THREE.BufferGeometry {
  const tube = 0.016 / 0.185;
  const parts: THREE.BufferGeometry[] = [new THREE.TorusGeometry(1, tube, 8, 40)];
  for (const angle of [0, Math.PI, -Math.PI / 2]) {
    const spoke = new THREE.BoxGeometry(0.92, 0.16, 0.07);
    spoke.translate(0.5, 0, -0.04); spoke.rotateZ(angle);
    parts.push(spoke);
  }
  const hub = new THREE.CylinderGeometry(0.3, 0.34, 0.22, 16);
  hub.rotateX(Math.PI / 2); hub.translate(0, 0, -0.1);
  parts.push(hub);
  const merged = mergeGeometries(parts.map(p => p.toNonIndexed()))!;
  for (const p of parts) p.dispose();
  return merged;
}

export class Drivers {
  private template: DriverTemplate | null = null;
  private readonly drivers = new Map<string, Driver>();
  private readonly free: Driver[] = [];
  private frameId = 0;
  private created = 0;
  private readonly wheelGeometry = wheelGeometry();
  private readonly wheelMaterial = new THREE.MeshStandardMaterial({ color: 0x1c1e21, roughness: 0.55, metalness: 0.05 });
  private readonly camera = new THREE.Vector3();
  private readonly at = new THREE.Vector3();
  private readonly basis = { fwd: { x: 0, y: 0, z: 1 }, left: { x: 1, y: 0, z: 0 }, up: { x: 0, y: 1, z: 0 } };
  /** Main-thread ms spent in finish() (smoothed), for the frame-cost measurement. */
  frameMs = 0;

  /** `prepare` builds the steering wheel's programs ahead (world.prepareGraphics); the character's are the player's. */
  constructor(private readonly prepare: (model: THREE.Object3D) => Promise<void> = async () => {}) {}

  /** The loaded player character (actors/player.ts `template`) every driver is cloned from. */
  setCharacter(scene: THREE.Object3D, clips: readonly THREE.AnimationClip[]) {
    this.template = new DriverTemplate(scene, clips);
    const probe = new THREE.Mesh(this.wheelGeometry, this.wheelMaterial);
    probe.userData.preparesDetached = true;
    void this.prepare(probe).catch(error => console.warn(`[drivers] steering wheel preparation failed: ${String(error)}`));
  }

  /** Seats the driver `key` this frame in `parent` (the vehicle's fleet-frame Object3D) with `layout`. */
  seat(key: string, parent: THREE.Object3D, layout: DriverLayout, input: DriverInput) {
    if (!this.template) return;
    let driver = this.drivers.get(key);
    // One seat per driver and frame: the first caller (the visual that draws the vehicle) keeps it.
    if (driver?.seatedFrame === this.frameId) return;
    if (!driver) {
      driver = this.free.pop() ?? this.create();
      driver.key = key; driver.motion.reset(); driver.drawn = false;
      this.drivers.set(key, driver);
    }
    if (driver.frame.parent !== parent) { parent.add(driver.frame); driver.motion.reset(); }
    if (driver.layout !== layout) this.useLayout(driver, layout);
    driver.seatedFrame = this.frameId; driver.input = input;
  }

  private create(): Driver {
    const frame = new THREE.Group(); frame.name = 'driver-seat';
    const rig = new DriverRig(this.template!);
    frame.add(rig.model);
    return { key: '', frame, rig, motion: new DriverMotion(), index: this.created++, wheel: null, spin: null, layout: null, seatedFrame: -1, input: null, drawn: false };
  }

  private useLayout(driver: Driver, layout: DriverLayout) {
    driver.layout = layout;
    driver.wheel?.removeFromParent(); driver.wheel = null; driver.spin = null;
    if (!layout.wheel.drawn) return;
    // The wheel frame: X the driver's right, Y 12 o'clock, Z toward the driver (back and up by the column tilt).
    const wheel = new THREE.Group(); wheel.name = 'driver-wheel';
    const c = layout.wheel.centre, t = layout.wheel.tilt;
    wheel.position.set(c[0], c[1], c[2]);
    wheel.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(new THREE.Vector3(-1, 0, 0),
      new THREE.Vector3(0, Math.cos(t), Math.sin(t)), new THREE.Vector3(0, Math.sin(t), -Math.cos(t))));
    const spin = new THREE.Mesh(this.wheelGeometry, this.wheelMaterial);
    spin.scale.setScalar(layout.wheel.radius); spin.castShadow = false; spin.receiveShadow = true;
    wheel.add(spin);
    driver.frame.add(wheel);
    driver.wheel = wheel; driver.spin = spin;
  }

  /** Animates the drivers seated this frame near `camera`, hides the others. Call after every seat(), before render. */
  finish(camera: THREE.Camera, now = performance.now()) {
    const t0 = performance.now();
    camera.getWorldPosition(this.camera);
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
      const near = distance < FULL_DISTANCE + (driver.drawn ? HYSTERESIS : 0);
      frame.visible = near;
      if (!near) { driver.drawn = false; driver.motion.reset(); continue; }
      driver.drawn = true;
      const e = frame.parent!.matrixWorld.elements;
      const unit = (x: number, y: number, z: number, out: Vec) => { const l = Math.hypot(x, y, z) || 1; out.x = x / l; out.y = y / l; out.z = z / l; return out; };
      const { fwd, left, up } = this.basis;
      unit(e[8], e[9], e[10], fwd); unit(e[0], e[1], e[2], left); unit(e[4], e[5], e[6], up);
      const steer = steerFraction(layout, input.wheels);
      const pose = driver.motion.update({ t: now / 1000, v: input.velocity, fwd, left, up, steer, impact: input.impact, horn: input.horn });
      driver.rig.detail(distance < FACE_DISTANCE, distance < LEG_DISTANCE);
      if (driver.wheel) driver.wheel.visible = distance < WHEEL_DISTANCE;
      if (distance > POSE_EVERY_OTHER && (this.frameId + driver.index) % 2) continue;
      // A vehicle with its own wheel node (the trophy truck) turns it by the same function (trophy4400/rig.ts).
      const angle = steeringAngle(layout.wheel, steer);
      if (driver.spin) driver.spin.rotation.z = angle;
      driver.rig.pose(frame, layout, pose, angle);
    }
    this.frameId++;
    this.frameMs += (performance.now() - t0 - this.frameMs) * 0.1;
  }

  /** World position of driver `key`'s hips while it is seated and drawn (the seated-vehicle snapshot). */
  hipsOf(key: string): number[] | null {
    const d = this.drivers.get(key);
    return d?.drawn && d.frame.visible ? d.rig.model.getObjectByName('DEF-hips')!.getWorldPosition(new THREE.Vector3()).toArray() : null;
  }

  /** Dev and test view: each seated driver, whether drawn, its hands' distance from the rim grips and motion. */
  snapshot() {
    return { frameMs: this.frameMs, pool: this.free.length, drivers: [...this.drivers.values()].map(d => ({
      key: d.key, drawn: d.drawn && d.frame.visible, cls: d.layout?.cls, reach: { ...d.rig.reach },
      hips: d.rig.model.getObjectByName('DEF-hips')?.getWorldPosition(new THREE.Vector3()).toArray(),
      head: d.rig.model.getObjectByName('DEF-head')?.getWorldPosition(new THREE.Vector3()).toArray(),
      pose: { ...d.motion.pose } })) };
  }
}
