import * as THREE from 'three/webgpu';
import type { V3 } from '../core/origin';
import { SCOOTER_ID, TRUCK_ID } from '../gameplay/missions/missions';
import { SPAWN_HEIGHT } from '../gameplay/saveGame';
import type { PhysicsClient } from '../physics/client';
import type { VehicleState } from '../physics/protocol';
import type { PlayerVisual } from './player';
import { ScooterVisual } from './scooter';
import { TruckVisual } from './truck';
import { configureTrophyAsset } from './trophy4400/asset';
import { frontSteer, type Drivers } from './driver/drivers';

/** The bought vehicles: the scooter (its rider is the player character, seated on its anchor) and the trophy truck
 *  (a seated driver, actors/driver/). Both exist as physics vehicles once bought (a loaded game places them in
 *  gameplay/saveGame.ts). */
export class SeatedVehicles {
  readonly scooter: ScooterVisual;
  readonly truck: TruckVisual;
  private readonly extraTrucks = new Map<string, TruckVisual>();
  private readonly byId: Record<string, ScooterVisual | TruckVisual>;

  /** `prepare` builds a model's shader programs ahead of its first draw (the actor lane of main.ts prepareGraphics). */
  constructor(private readonly root: THREE.Object3D, private readonly physics: Pick<PhysicsClient, 'spawnVehicle'>,
    private readonly groundAt: (x: number, z: number) => Promise<number>,
    private readonly prepare: (model: THREE.Object3D) => Promise<void> = async () => {}) {
    this.scooter = new ScooterVisual(root);
    this.truck = new TruckVisual(root);
    this.byId = { [SCOOTER_ID]: this.scooter, [TRUCK_ID]: this.truck };
  }

  /** `renderer` decodes the trucks' KTX2 textures (required in the game; tests load without textures).
   *  Both vehicles stay hidden until one is bought, spawned or seen under a remote player, so the boot warm-up never
   *  draws them. Each loaded model is prepared at once instead: unprepared, its first draws are skipped until the
   *  deferred first-use builds (render/asyncNodeBuilds.ts) reach its programs, which on city.lab took minutes (the
   *  driver sat in the air). Extra, draft and remote trucks share the truck template's materials and programs. */
  load(base: string, renderer?: THREE.WebGPURenderer) {
    configureTrophyAsset(base, renderer);
    const prepared = (visual: ScooterVisual | TruckVisual, load: Promise<void>) => load.then(() => {
      this.prepare(visual.root).catch(error => console.warn(`[assets] ${visual.root.name} shader preparation failed: ${String((error as Error)?.message ?? error)}`));
    });
    return Promise.all([prepared(this.scooter, this.scooter.load(`${base}assets/vehicles/scooter.glb`)), prepared(this.truck, this.truck.load())]);
  }

  async addTruck(id: string) {
    if (this.byId[id]) return;
    const truck = new TruckVisual(this.root);
    await truck.load();
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

  /** The seated visual for physics vehicle `id`; undefined for a car (drawn by actors/car.ts or the fleet). */
  of(id: string | null | undefined) { return id ? this.byId[id] : undefined; }
  /** The player character itself rides vehicle `id` (the scooter); every other driver is a seated clone. */
  rides(id: string | null | undefined) { return this.of(id) === this.scooter; }

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

  /** Follows each vehicle's latest state, seats the player on the scooter and the driver in a driven truck. */
  update(dt: number, latestOf: (id: string) => VehicleState | null, drivenId: string | null, player: PlayerVisual, drivers?: Drivers) {
    const scooter = latestOf(SCOOTER_ID), truck = latestOf(TRUCK_ID);
    this.scooter.root.visible = !!scooter;
    if (scooter) this.scooter.update(dt, scooter);
    this.truck.root.visible = !!truck;
    if (truck) this.truck.update(truck);
    for (const [id, actor] of this.extraTrucks) {
      const state = latestOf(id);
      if (state) actor.update(state);
      else actor.root.visible = false;
    }
    const seat = this.of(drivenId), state = drivenId ? latestOf(drivenId) : null;
    if (seat === this.scooter && state) player.ride(dt, seat.seat);
    else if (seat instanceof TruckVisual && state && seat.loaded && seat.driverLayout && drivers) {
      drivers.seat('local', seat.driverFrame, seat.driverLayout, { velocity: state.linvel, steer: frontSteer(state.wheels), impact: state.impact?.id });
    }
  }

  /** Test and debug view: visibility, the scooter's lean and where the rider or driver sits (`driver`: the local
   *  driver's hips, actors/driver/drivers.ts, while one is seated and drawn). */
  snapshot(player: PlayerVisual, drivenId: string | null, driver?: number[] | null) {
    const riding = player.root.visible && player.locomotion === 'ride';
    const truck = !!drivenId && (drivenId === TRUCK_ID || this.extraTrucks.has(drivenId));
    return {
      scooter: { visible: this.scooter.root.visible, lean: this.scooter.leanAngle, rider: riding,
        riderPos: player.root.position.toArray(), seatPos: this.scooter.seat.getWorldPosition(new THREE.Vector3()).toArray() },
      truck: { visible: this.truck.root.visible, driver: truck && !!driver, driverPos: truck ? driver ?? null : null,
        seatPos: this.truck.seat.getWorldPosition(new THREE.Vector3()).toArray() },
    };
  }
}
