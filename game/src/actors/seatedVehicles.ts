import * as THREE from 'three/webgpu';
import type { V3 } from '../core/origin';
import { SCOOTER_ID, TRUCK_ID } from '../gameplay/missions/missions';
import { SPAWN_HEIGHT } from '../gameplay/saveGame';
import type { PhysicsClient } from '../physics/client';
import type { VehicleState } from '../physics/protocol';
import type { PlayerVisual } from './player';
import { ScooterVisual } from './scooter';
import { TruckVisual } from './truck';

/** The bought vehicles whose rider or driver stays visible, seated on its anchor: the scooter and the trophy truck.
 *  Both exist as physics vehicles once bought (a loaded game places them in gameplay/saveGame.ts). */
export class SeatedVehicles {
  readonly scooter: ScooterVisual;
  readonly truck: TruckVisual;
  private readonly extraTrucks = new Map<string, TruckVisual>();
  private readonly byId: Record<string, ScooterVisual | TruckVisual>;

  constructor(private readonly root: THREE.Object3D, private readonly physics: Pick<PhysicsClient, 'spawnVehicle'>,
    private readonly groundAt: (x: number, z: number) => Promise<number>) {
    this.scooter = new ScooterVisual(root);
    this.truck = new TruckVisual(root);
    this.byId = { [SCOOTER_ID]: this.scooter, [TRUCK_ID]: this.truck };
  }

  load(base: string) {
    return Promise.all([this.scooter.load(`${base}assets/vehicles/scooter.glb`), this.truck.load(`${base}assets/vehicles/truck.glb`)]);
  }

  async addTruck(id: string) {
    if (this.byId[id]) return;
    const truck = new TruckVisual(this.root);
    await truck.load(`${import.meta.env.BASE_URL}assets/vehicles/truck.glb`);
    this.extraTrucks.set(id, truck);
    this.byId[id] = truck;
  }

  /** Untouched draft trucks leave with their source tile; bought/borrowed vehicles remain. */
  retainTrucks(ids: ReadonlySet<string>) {
    for (const [id, truck] of this.extraTrucks) {
      if (ids.has(id)) continue;
      truck.dispose(); this.extraTrucks.delete(id); delete this.byId[id];
    }
  }

  /** The seated visual for physics vehicle `id`; undefined for a car, whose driver disappears into it. */
  of(id: string | null | undefined) { return id ? this.byId[id] : undefined; }

  /** Spawns (or moves) the bought `kind` onto the ground at `spot`. */
  async park(kind: 'scooter' | 'truck', spot: V3 & { yaw: number }) {
    const y = await this.groundAt(spot.x, spot.z) + SPAWN_HEIGHT[kind];
    this.physics.spawnVehicle({ x: spot.x, y, z: spot.z }, spot.yaw, { id: kind === 'truck' ? TRUCK_ID : SCOOTER_ID, kind });
  }

  /** Developer spawn: the bought `kind` on the ground `metres` ahead of `at` along the camera's view, facing that way. */
  parkAhead(kind: 'scooter' | 'truck', at: V3, camera: THREE.Camera, metres = 7) {
    const view = camera.getWorldDirection(new THREE.Vector3()).setY(0).normalize();
    return this.park(kind, { x: at.x + view.x * metres, y: at.y, z: at.z + view.z * metres, yaw: Math.atan2(-view.z, view.x) });
  }

  /** Follows each vehicle's latest state and seats the player on the one being driven. */
  update(dt: number, latestOf: (id: string) => VehicleState | null, drivenId: string | null, player: PlayerVisual) {
    const scooter = latestOf(SCOOTER_ID), truck = latestOf(TRUCK_ID);
    if (scooter) this.scooter.update(dt, scooter);
    if (truck) this.truck.update(truck);
    for (const [id, actor] of this.extraTrucks) {
      const state = latestOf(id);
      if (state) actor.update(state);
      else actor.root.visible = false;
    }
    const seat = this.of(drivenId);
    if (seat && latestOf(drivenId!)) player.ride(dt, seat.seat);
  }

  /** Test and debug view: visibility, the scooter's lean and where the rider or driver sits. */
  snapshot(player: PlayerVisual, drivenId: string | null) {
    const riding = player.root.visible && player.locomotion === 'ride';
    return {
      scooter: { visible: this.scooter.root.visible, lean: this.scooter.leanAngle, rider: riding,
        riderPos: player.root.position.toArray(), seatPos: this.scooter.seat.getWorldPosition(new THREE.Vector3()).toArray() },
      truck: { visible: this.truck.root.visible, driver: riding && !!drivenId && (drivenId === TRUCK_ID || this.extraTrucks.has(drivenId)),
        seatPos: this.truck.seat.getWorldPosition(new THREE.Vector3()).toArray() },
    };
  }
}
