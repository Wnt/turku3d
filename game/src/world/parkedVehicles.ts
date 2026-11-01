import type { ParkedCarVisuals } from '../actors/car';
import type { SeatedVehicles } from '../actors/seatedVehicles';
import { collisionTileAt, type V3 } from '../core/origin';
import type { PhysicsClient } from '../physics/client';
import type { Quat, VehicleState } from '../physics/protocol';
import type { Manifest, ParkedVehicle } from './manifest';
import { slotShown } from './parkedOccupancy';

/** Physics chassis pose: centre position and rotation, as the worker reports them. */
export interface ParkedPose { pos: V3; rot: Quat }

const SPAWN_RADIUS = 120, DESPAWN_RADIUS = 170;
/** A capped body moves to a closer car only when that car is at least 15% and 4 m nearer. Undoing
 * a move then takes more than 4 m of player travel, so jitter cannot flip cars between physical
 * and visual, while a visual car stays beyond the 250 ms reach even in a dense lot. */
const SWAP_RATIO = 0.85, SWAP_MARGIN = 4;
/** A rolling car keeps its body until it rests; it never freezes mid-motion. */
const RESTING_SPEED = 0.3;
/** Re-rank faster while the cap leaves nearby cars visual, so the player cannot reach one first. */
const SYNC_MS = 1000, SATURATED_SYNC_MS = 250;

const chassisLift = (car: ParkedVehicle) => car.model === 'truck' ? 1 : .75;

/** Chassis rotation of a draft slot: heading (chassis +X forward), then the ground's pitch (nose up,
 * about the chassis' right +Z) and roll (leaning right, about +X) the generator measured. */
export function slotRotation(car: Pick<ParkedVehicle, 'yaw' | 'pitch' | 'roll'>): Quat {
  const y = (car.yaw - Math.PI / 2) / 2, p = (car.pitch ?? 0) / 2, r = (car.roll ?? 0) / 2;
  const sy = Math.sin(y), cy = Math.cos(y), sp = Math.sin(p), cp = Math.cos(p), sr = Math.sin(r), cr = Math.cos(r);
  // (0, sy, 0, cy) * (0, 0, sp, cp) * (sr, 0, 0, cr)
  const qx = sy * sp, qy = sy * cp, qz = cy * sp, qw = cy * cp;
  return { x: qw * sr + qx * cr, y: qy * cr + qz * sr, z: qz * cr - qy * sr, w: qw * cr - qx * sr };
}

/** Drafts supply identities; only the nearest `cap` collision-backed cars within 120 m become
 * physics actors (trucks are rare and exempt; their visual exists only with a body). The rest
 * stay visual at their last physical pose, or at their draft slot. Borrowed cars retain their
 * appearance and identity when their source tile leaves. A slot whose occupancy draw is above the
 * hour's occupancy (world/parkedOccupancy.ts) is empty: no visual, and no body once it rests. */
export class ParkedVehicles {
  private spawned = new Map<string, ParkedVehicle>();
  private borrowed = new Map<string, ParkedVehicle>();
  private rest = new Map<string, ParkedPose>();
  /** Draft slot poses, computed once per draft record (the visual reads one per car every frame). */
  private slots = new WeakMap<ParkedVehicle, ParkedPose>();
  private sharedKey = '';
  private shared = new Set<string>();
  private owned = new Set<string>();
  private inventory = '';
  private busy = false;
  private saturated = false;
  private last = -Infinity;
  private night = 0;

  constructor(private manifest: Manifest, private physics: Pick<PhysicsClient, 'loaded' | 'latest' | 'spawnVehicle' | 'removeVehicle'>,
    private visuals: Pick<ParkedCarVisuals, 'load'>, private seated: Pick<SeatedVehicles, 'addTruck' | 'retainTrucks'>,
    private readonly cap = 60) {}

  resetRound() {
    for (const id of new Set([...this.spawned.keys(), ...this.borrowed.keys()])) this.physics.removeVehicle(id);
    this.spawned.clear(); this.borrowed.clear(); this.rest.clear(); this.shared.clear(); this.owned.clear();
    this.sharedKey = ''; this.last = -Infinity; this.saturated = false;
  }

  setShared(sources: ReadonlySet<string>, owned: ReadonlySet<string>) {
    const key = JSON.stringify([[...sources].sort(), [...owned].sort()]);
    if (key === this.sharedKey) return;
    this.sharedKey = key;
    this.shared = new Set(sources); this.owned = new Set(owned);
    for (const id of sources) if (!owned.has(id)) {
      this.physics.removeVehicle(id); this.spawned.delete(id); this.borrowed.delete(id); this.rest.delete(id);
    }
    for (const id of owned) {
      const car = this.manifest.vehicles?.find(v => v.object_id === id);
      if (car) this.borrowed.set(id, car);
    }
  }

  /** Night level of the shared clock (parkedNight); it decides which slots hold cars. */
  setNight(level: number) { this.night = level; }

  /** Where a draft car without a physics state is drawn; null when another owner renders it or the
   * slot is empty at this hour. */
  restingPose(car: ParkedVehicle): ParkedPose | null {
    const id = car.object_id;
    if (this.shared.has(id) || this.borrowed.has(id) || !slotShown(car, this.night)) return null;
    const rest = this.rest.get(id);
    if (rest) return rest;
    return this.slot(car);
  }

  private slot(car: ParkedVehicle): ParkedPose {
    let slot = this.slots.get(car);
    if (!slot) {
      slot = { pos: { x: car.x, y: car.y + chassisLift(car), z: car.z }, rot: slotRotation(car) };
      this.slots.set(car, slot);
    }
    return slot;
  }

  update(x: number, z: number, driven: string | null, now: number) {
    if (driven) {
      const car = this.manifest.vehicles?.find(v => v.object_id === driven) ?? this.spawned.get(driven);
      if (car) this.borrowed.set(driven, car);
    }
    if (this.busy || now - this.last < (this.saturated ? SATURATED_SYNC_MS : SYNC_MS)) return;
    this.last = now;
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
      const states = new Map((this.physics.latest?.vehicles ?? []).map(v => [v.id, v]));
      const distance = (id: string, car: ParkedVehicle) => {
        const at = states.get(id)?.pos ?? this.rest.get(id)?.pos ?? car;
        return Math.hypot(at.x - x, at.z - z);
      };
      for (const [id, car] of this.spawned) {
        if (this.owned.has(id) || this.borrowed.has(id)) continue;
        const rolling = (states.get(id)?.speed ?? 0) >= RESTING_SPEED;
        if (cars.has(id) && distance(id, car) < DESPAWN_RADIUS && (rolling || slotShown(car, this.night))) continue;
        this.despawn(id, states);
      }
      for (const id of this.rest.keys()) if (!cars.has(id)) this.rest.delete(id);

      const candidates: { id: string; car: ParkedVehicle; d: number }[] = [];
      for (const [id, car] of cars) {
        if (this.shared.has(id) || this.spawned.has(id) || states.has(id) || this.borrowed.has(id)) continue;
        if (!slotShown(car, this.night)) continue;
        const d = distance(id, car);
        if (d <= SPAWN_RADIUS && this.supported(id, car)) candidates.push({ id, car, d });
      }
      candidates.sort((a, b) => a.d - b.d);
      for (const { id, car } of candidates) if (car.model === 'truck') this.spawn(id, car);

      // Bodies under the cap, farthest first; rolling cars are not offered for eviction.
      const capped = [...this.spawned].filter(([id, car]) => !this.owned.has(id) && !this.borrowed.has(id) && car.model !== 'truck');
      let count = capped.length;
      const evictable = capped.filter(([id]) => (states.get(id)?.speed ?? 0) < RESTING_SPEED)
        .map(([id, car]) => ({ id, d: distance(id, car) })).sort((a, b) => b.d - a.d);
      for (; count > this.cap && evictable.length; count--) this.despawn(evictable.shift()!.id, states);
      this.saturated = false;
      for (const { id, car, d } of candidates) {
        if (car.model === 'truck') continue;
        if (count < this.cap) { this.spawn(id, car); count++; continue; }
        const far = evictable[0];
        if (!far || d >= Math.min(far.d * SWAP_RATIO, far.d - SWAP_MARGIN)) { this.saturated = true; break; }
        evictable.shift();
        this.despawn(far.id, states);
        this.spawn(id, car);
      }
    } finally { this.busy = false; }
  }

  /** All footprint corners must have resident collision before the worker can settle it. */
  private supported(id: string, car: ParkedVehicle) {
    const at = this.rest.get(id)?.pos ?? car;
    const r = Math.hypot(car.width, car.length) / 2;
    return [-r, r].every(dx => [-r, r].every(dz => {
      const cell = collisionTileAt(at.x + dx, at.z + dz);
      return this.physics.loaded.has(`c_${cell.i}_${cell.j}`);
    }));
  }

  private spawn(id: string, car: ParkedVehicle) {
    const truck = car.model === 'truck', pose = this.rest.get(id) ?? this.slot(car);
    const spec = { id, parked: true, kind: truck ? 'truck' as const : 'car' as const,
      ...(truck ? {} : { halfExt: { x: car.length / 2, y: car.height * .28, z: car.width / 2 } }), rot: pose.rot };
    this.physics.spawnVehicle(pose.pos, car.yaw - Math.PI / 2, spec);
    this.spawned.set(id, car);
  }

  /** The visual keeps the body's last pose; a later body resumes from it. */
  private despawn(id: string, states: ReadonlyMap<string | undefined, VehicleState>) {
    const state = states.get(id);
    if (state) this.rest.set(id, { pos: { ...state.pos }, rot: { ...state.rot } });
    this.physics.removeVehicle(id);
    this.spawned.delete(id);
  }
}
