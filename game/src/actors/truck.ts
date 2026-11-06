import * as T from 'three/webgpu';
import { TRUCK } from '../physics/constants';
import { vehicleLamps } from '../vehicle/lamps';
import type { VehicleState } from '../physics/protocol';
import { instance, trophyAsset } from './trophy4400/asset';
import { GROUND_Y, applyTrophyRig, staticWheels, useTrophyAsset } from './trophy4400/rig';
import { trophyDriverLayout, type DriverLayout } from './driver/layout';

/** The #19-style 4400 trophy truck (SPEC; model in public/assets/vehicles/trophy4400). The root follows the chassis
 *  (physics frame: +X forward, +Y up, wheel order FL/FR/RL/RR); the GLB model sits under it at its ground origin.
 *  Geometry, materials and textures are shared by every local and remote truck; the rig's bones are per actor. */
export class TruckVisual {
  readonly root = new T.Group();
  readonly seat = new T.Object3D();
  /** The driver's frame (actors/driver): the fleet convention (+Z forward, +X left) on the ground under the truck. */
  readonly driverFrame = new T.Group();
  driverLayout: DriverLayout | null = null;
  loaded = false;

  constructor(scene: T.Object3D) {
    this.root.name = 'truck'; this.root.visible = false;
    this.driverFrame.name = 'driver-frame'; this.driverFrame.position.y = GROUND_Y; this.driverFrame.rotation.y = Math.PI / 2;
    this.root.add(this.seat, this.driverFrame); scene.add(this.root);
  }

  async load() {
    if (this.loaded) return;
    const asset = await trophyAsset();
    useTrophyAsset(asset);
    const model = instance(asset);
    model.position.y = GROUND_Y;
    this.root.add(model);
    // The driver's H-point, facing +X like the chassis (the character model faces +Z).
    const seat = asset.rest.get('seat-driver')!;
    this.seat.position.set(seat.p.x, seat.p.y + GROUND_Y, seat.p.z); this.seat.rotation.set(0, Math.PI / 2, 0);
    const wheel = asset.rest.get('steer-wheel');
    if (wheel) this.driverLayout = trophyDriverLayout(seat.p, wheel.p, new T.Vector3(0, 1, 0).applyQuaternion(wheel.q), TRUCK.maxSteer);
    // Physical hub markers (remote bounds and checks read `vehicle-wheel-*`).
    for (let i = 0; i < TRUCK.wheels.length; i++) { const marker = new T.Object3D(); marker.name = `vehicle-wheel-${i}`; this.root.add(marker); }
    // The lamp lenses' own materials exist before the model is prepared (actors/seatedVehicles.ts), so those are drawn.
    vehicleLamps(this.root, 'truck', 0);
    applyTrophyRig(this.root, staticWheels());
    this.loaded = true;
  }

  dispose() {
    // Geometry, materials and textures belong to the shared template; only this actor's bones go.
    this.root.removeFromParent();
  }

  update(s: VehicleState) {
    this.root.visible = this.loaded;
    this.root.position.set(s.pos.x, s.pos.y, s.pos.z);
    this.root.quaternion.set(s.rot.x, s.rot.y, s.rot.z, s.rot.w);
    vehicleLamps(this.root, 'truck', s.lights ?? 0);
    applyTrophyRig(this.root, s.wheels);
  }
}
