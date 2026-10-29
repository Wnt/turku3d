import type { ParkedCarVisuals } from '../actors/car';
import type { SeatedVehicles } from '../actors/seatedVehicles';
import { collisionTileAt } from '../core/origin';
import type { PhysicsClient } from '../physics/client';
import type { Manifest, ParkedVehicle } from './manifest';

/** Drafts supply identities; only nearby collision-backed cars become physics actors.
 * Borrowed cars retain their appearance and identity when their source tile leaves. */
export class ParkedVehicles {
  private spawned = new Map<string, ParkedVehicle>();
  private borrowed = new Map<string, ParkedVehicle>();
  private sharedKey = '';
  private shared = new Set<string>();
  private owned = new Set<string>();
  private inventory = '';
  private busy = false;
  private next = 0;

  constructor(private manifest: Manifest, private physics: Pick<PhysicsClient, 'loaded' | 'latest' | 'spawnVehicle' | 'removeVehicle'>,
    private visuals: Pick<ParkedCarVisuals, 'load'>, private seated: Pick<SeatedVehicles, 'addTruck' | 'retainTrucks'>) {}

  setShared(sources: ReadonlySet<string>, owned: ReadonlySet<string>) {
    const key = JSON.stringify([[...sources].sort(), [...owned].sort()]);
    if (key === this.sharedKey) return;
    this.sharedKey = key;
    this.shared = new Set(sources); this.owned = new Set(owned);
    for (const id of sources) if (!owned.has(id)) {
      this.physics.removeVehicle(id); this.spawned.delete(id); this.borrowed.delete(id);
    }
    for (const id of owned) {
      const car = this.manifest.vehicles?.find(v => v.object_id === id);
      if (car) this.borrowed.set(id, car);
    }
  }

  update(x: number, z: number, driven: string | null, now: number) {
    if (driven) {
      const car = this.manifest.vehicles?.find(v => v.object_id === driven) ?? this.spawned.get(driven);
      if (car) this.borrowed.set(driven, car);
    }
    if (this.busy || now < this.next) return;
    this.next = now + 1000;
    void this.sync(x, z).catch(error => console.warn('[parked]', error));
  }

  private async sync(x: number, z: number) {
    this.busy = true;
    try {
      const cars = new Map((this.manifest.vehicles ?? []).map(v => [v.object_id, v]));
      for (const [id, car] of this.borrowed) if (!cars.has(id)) cars.set(id, car);
      this.manifest.vehicles = [...cars.values()];
      const key = [...cars.values()].map(v => `${v.object_id}/${v.model}/${v.color}`).sort().join('|');
      if (key !== this.inventory) {
        await this.visuals.load(this.manifest);
        await Promise.all([...cars.values()].filter(v => v.model === 'truck').map(v => this.seated.addTruck(v.object_id)));
        this.seated.retainTrucks(new Set(cars.keys()));
        this.inventory = key;
      }
      const present = new Set(this.physics.latest?.vehicles.map(v => v.id));
      for (const [id, car] of cars) {
        if (this.shared.has(id) || this.spawned.has(id) || present.has(id) || this.borrowed.has(id)) continue;
        if (Math.hypot(car.x - x, car.z - z) > 120) continue;
        // All footprint corners must have resident collision before the worker can settle it.
        const r = Math.hypot(car.width, car.length) / 2;
        const supported = [-r, r].every(dx => [-r, r].every(dz => {
          const cell = collisionTileAt(car.x + dx, car.z + dz);
          return this.physics.loaded.has(`c_${cell.i}_${cell.j}`);
        }));
        if (!supported) continue;
        const truck = car.model === 'truck';
        this.physics.spawnVehicle({ x: car.x, y: car.y + (truck ? 1 : .75), z: car.z }, car.yaw - Math.PI / 2,
          { id, parked: true, kind: truck ? 'truck' : 'car', ...(truck ? {} : { parked: true, halfExt: { x: car.length / 2, y: car.height * .28, z: car.width / 2 } }) });
        this.spawned.set(id, car);
      }
      for (const [id, car] of this.spawned) {
        if (this.owned.has(id) || this.borrowed.has(id)) continue;
        if (cars.has(id) && Math.hypot(car.x - x, car.z - z) < 170) continue;
        this.physics.removeVehicle(id);
        this.spawned.delete(id);
      }
    } finally { this.busy = false; }
  }
}
