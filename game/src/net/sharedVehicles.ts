import type { V3 } from '../core/origin';
import type { PhysicsClient } from '../physics/client';
import { VEHICLES } from '../physics/constants';
import type { VehicleState } from '../physics/protocol';
import type { Manifest } from '../world/manifest';
import type { ParkedVehicles } from '../world/parkedVehicles';
import { driverSide, yawOf } from '../gameplay/saveGame';
import { vehicleIdentity } from './identity';
import { VEHICLE_POSE_HEIGHT } from './height';
import type { MultiplayerClient, NetPose } from './client';
import type { SharedVehicle, VehicleCommand, VehicleUpdate } from './vehicleTypes';

export function vehiclePose(state: VehicleState, model: string, now: number): NetPose {
  const yaw = yawOf(state.rot) - Math.PI / 2;
  return { t: now, E: state.pos.x, N: -state.pos.z, H: state.pos.y - VEHICLE_POSE_HEIGHT,
    yaw: Math.atan2(Math.sin(yaw), Math.cos(yaw)), rot: state.rot, wheels: state.wheels,
    vE: state.linvel.x, vN: -state.linvel.z, vH: state.linvel.y,
    mode: state.kind ?? 'car', vehicle: model, carrying: false };
}

// Quantization is a dirty-state threshold, not physics: sleeping cars produce no
// updates; tiny suspension/float noise must not multiply into 16-player traffic.
export function vehicleFingerprint(p: NetPose): string {
  return JSON.stringify([p.E, p.N, p.H, p.vE, p.vN, p.vH].map(v => Math.round(v * 100)))
    + JSON.stringify(p.rot && [p.rot.x, p.rot.y, p.rot.z, p.rot.w].map(v => Math.round(v * 1000)))
    + JSON.stringify(p.wheels?.map(w => [w.steer, w.rotation, w.suspension].map(v => Math.round(v * 100))));
}

export const SEAT_CONFLICT_GRACE_MS = 3000;
interface DrivenSeat {
  local: string; id: string; vehicle: SharedVehicle | null;
  reclaim: boolean; pending: boolean; retryAt: number;
  conflict: { holder: string; since: number } | null;
}

/** Exactly one local body per owned shared vehicle; replicas never enter Rapier's vehicle solver. */
export class SharedVehicles {
  private managed = new Map<string, { local: string; epoch: number }>();
  private starterAt: V3 | null = null;
  private fresh = new Set<string>();
  private replicas = new Set<string>();
  private pending = new Set<string>();
  private retryAt = new Map<string, number>();
  private sent = new Map<string, string>();
  private impulses = new Map<string, number>();
  private blockedUntil = new Map<string, number>();
  private active = false;
  private entering = false;
  private driving: DrivenSeat | null = null;
  private releaseOnReconnect = new Set<string>();

  constructor(readonly client: MultiplayerClient, private physics: PhysicsClient,
    private manifest: Manifest, private parked: ParkedVehicles) {
    client.addEventListener('welcome', () => {
      if (this.driving) {
        this.driving.reclaim = true; this.driving.retryAt = 0; this.driving.conflict = null;
      }
      for (const id of this.releaseOnReconnect) void client.vehicle({ action: 'release', id });
      this.releaseOnReconnect.clear();
    });
    client.addEventListener('seatTaken', event => {
      const v = (event as CustomEvent<SharedVehicle>).detail, d = this.driving;
      if (!d || v.id !== d.id || !v.occupant || v.occupant === client.state.you?.id) return;
      if (d.conflict?.holder !== v.occupant) d.conflict = { holder: v.occupant, since: performance.now() };
    });
    client.addEventListener('vehicleImpulse', event => {
      if (!this.active) return;
      const m = (event as CustomEvent<{ id: string; epoch: number; sequence: number; E: number; N: number }>).detail;
      const v = client.state.vehicles.get(m.id);
      if (!v || v.owner !== client.state.you?.id || v.epoch !== m.epoch || m.sequence <= (this.impulses.get(m.id) ?? 0)) return;
      this.sync();
      this.impulses.set(m.id, m.sequence);
      this.blockedUntil.set(m.id, performance.now() + 100);
      physics.vehicleImpulse(this.localId(v), { x: m.E, y: 0, z: -m.N });
    });
  }

  localId(v: SharedVehicle): string {
    if (v.id === this.driving?.id) return this.driving.local;
    return v.creator === this.client.state.you?.id ? v.localId : v.id;
  }
  record(localId: string | null): SharedVehicle | undefined {
    return [...this.client.state.vehicles.values()].find(v => this.localId(v) === localId);
  }
  hasSeat(localId: string): boolean {
    const self = this.client.state.you?.id;
    return !!self && this.record(localId)?.occupant === self;
  }
  drive(local: string) {
    if (this.driving?.local === local) return;
    const vehicle = this.record(local) ?? this.client.state.vehicles.get(local) ?? null;
    const state = this.physics.latest?.vehicles.find(v => v.id === local);
    if (!vehicle && !state) return;
    const id = vehicle?.id ?? this.command(state!, 'acquire').id;
    this.driving = { local, id, vehicle, reclaim: true, pending: false, retryAt: 0, conflict: null };
    this.client.holdSeat(id);
  }
  release(localId: string | null) {
    const d = this.driving?.local === localId ? this.driving : null;
    const v = this.record(localId) ?? d?.vehicle;
    if (d || v?.occupant === this.client.state.you?.id) {
      const id = d?.id ?? v!.id;
      if (this.client.state.connected) void this.client.vehicle({ action: 'release', id });
      else this.releaseOnReconnect.add(id);
    }
    if (d) { this.driving = null; this.client.holdSeat(null); }
  }
  stop(localId: string | null) { this.release(localId); this.active = false; }

  private command(state: VehicleState, action: VehicleCommand['action']): VehicleCommand {
    const localId = state.id!;
    const source = this.manifest.vehicles?.find(v => v.object_id === localId);
    const kind = state.kind ?? 'car';
    const model = vehicleIdentity(kind, localId, this.manifest.vehicles ?? [])!;
    return { action, id: source ? `park:${source.object_id}` : `${this.client.state.you!.id}:${localId}`,
      source: source?.object_id ?? null, localId,
      half: source && kind === 'car' ? { x: source.length / 2, y: source.height * .28, z: source.width / 2 } : VEHICLES[kind].halfExt,
      pose: vehiclePose(state, model, performance.now()) };
  }

  async acquire(localId: string): Promise<string | null> {
    if (this.entering || !this.client.state.connected) return null;
    this.entering = true;
    try {
      const existing = this.record(localId) ?? this.client.state.vehicles.get(localId);
      const state = this.physics.latest?.vehicles.find(v => v.id === localId);
      if (!existing && !state) return null;
      const command = existing ? { action: 'acquire' as const, id: existing.id } : this.command(state!, 'acquire');
      if (!existing) this.fresh.add(command.id);
      const result = await this.client.vehicle(command);
      if (!result || !this.active) return null;
      this.drive(this.localId(result));
      this.sync();
      return this.localId(result);
    } finally { this.entering = false; }
  }

  /** Missing/empty snapshots are uncertainty, never permission to eject. */
  private reclaimSeat(now: number) {
    const d = this.driving, self = this.client.state.you?.id;
    if (!d || !self || !this.client.state.connected) return;
    const v = this.client.state.vehicles.get(d.id);
    if (v) d.vehicle = v;
    if (!v?.occupant || v.occupant === self || (d.conflict && d.conflict.holder !== v.occupant)) d.conflict = null;
    if ((!d.reclaim && v?.occupant === self) || d.pending || now < d.retryAt) return;
    const state = this.physics.latest?.vehicles.find(s => s.id === d.local);
    let command: VehicleCommand;
    if (v) command = { action: 'acquire', id: v.id };
    else {
      if (!state) return;
      const cached = d.vehicle;
      command = cached ? { action: 'acquire', id: cached.source ? cached.id : `${self}:${d.local}`,
        source: cached.source, localId: d.local, half: cached.half,
        pose: vehiclePose(state, cached.pose.vehicle!, now) } : this.command(state, 'acquire');
      d.id = command.id;
      this.client.holdSeat(d.id);
    }
    d.pending = true; d.retryAt = now + 1000;
    void this.client.vehicle(command).then(result => {
      d.pending = false;
      if (this.driving !== d) return;
      if (result && this.client.state.vehicles.get(result.id)?.occupant === self) {
        d.vehicle = result; d.reclaim = false; d.conflict = null;
      }
    });
  }

  /** A confirmed competing holder gets a grace period; use its current chassis,
   * even if our body/registry briefly disappeared. Never use the old walking feet. */
  seatExit(now: number): V3 | null {
    const d = this.driving, conflict = d?.conflict;
    if (!d || !conflict || !this.client.state.connected || now - conflict.since < SEAT_CONFLICT_GRACE_MS) return null;
    const v = this.client.state.vehicles.get(d.id);
    if (!v || v.occupant !== conflict.holder) return null;
    const angle = (v.pose.yaw + Math.PI / 2) / 2;
    return driverSide({ pos: { x: v.pose.E, y: v.pose.H + VEHICLE_POSE_HEIGHT, z: -v.pose.N },
      rot: v.pose.rot ?? { x: 0, y: Math.sin(angle), z: 0, w: Math.cos(angle) },
      kind: v.pose.mode as 'car' | 'scooter' | 'truck' });
  }

  nearest(at: V3): { id: string; distance: number } | null {
    const candidates = [...this.client.state.vehicles.values()].filter(v => !v.occupant).flatMap(v => {
      const local = v.owner === this.client.state.you?.id ? this.physics.latest?.vehicles.find(s => s.id === this.localId(v)) : null;
      const point = local?.pos ?? { x: v.pose.E, y: v.pose.H, z: -v.pose.N };
      return Math.abs(point.y - at.y) < 3 ? [{ id: this.localId(v), distance: Math.hypot(point.x - at.x, point.z - at.z) }] : [];
    });
    return candidates.sort((a, b) => a.distance - b.distance)[0] ?? null;
  }

  sync() {
    const self = this.client.state.you?.id;
    if (!self || !this.active || !this.client.state.connected) return;
    const owned = new Set<string>();
    for (const v of this.client.state.vehicles.values()) {
      if (v.owner !== self) {
        if (this.localId(v) === this.driving?.local) continue;
        if (v.creator === self && !this.replicas.has(v.id)) {
          this.physics.removeVehicle(this.localId(v)); this.replicas.add(v.id);
        }
        continue;
      }
      this.replicas.delete(v.id);
      if (v.source && this.localId(v) === v.source) owned.add(v.source);
      const local = this.localId(v), old = this.managed.get(v.id);
      if (old?.epoch === v.epoch) continue;
      const present = this.physics.latest?.vehicles.some(s => s.id === local);
      // Preserve the just-registered local body. A transferred/rejoined body uses
      // its exact last quaternion and velocity, including a flipped parked car.
      if (!present || (local !== this.driving?.local && (old || !this.fresh.has(v.id) || v.creator !== self))) {
        this.blockedUntil.set(v.id, performance.now() + 100);
        this.physics.spawnVehicle(
        { x: v.pose.E, y: v.pose.H + VEHICLE_POSE_HEIGHT, z: -v.pose.N }, v.pose.yaw + Math.PI / 2,
        { id: local, kind: v.pose.mode as 'car' | 'scooter' | 'truck', halfExt: v.half, rot: v.pose.rot,
          linvel: { x: v.pose.vE, y: v.pose.vH, z: -v.pose.vN } });
      }
      this.fresh.delete(v.id);
      for (const [id, item] of this.managed) if (id !== v.id && item.local === local) {
        this.managed.delete(id); this.sent.delete(id);
      }
      this.managed.set(v.id, { local, epoch: v.epoch }); this.sent.delete(v.id);
    }
    for (const [id, item] of this.managed) if (this.client.state.vehicles.get(id)?.owner !== self) {
      if (item.local === this.driving?.local) continue;
      this.physics.removeVehicle(item.local); this.retryAt.set(item.local, performance.now() + 10000); this.managed.delete(id); this.sent.delete(id);
    }
    const source = this.driving?.vehicle?.source;
    if (source && source === this.driving?.local) owned.add(source);
    this.parked.setShared(source ? new Set([...this.client.state.vehicleSources, source]) : this.client.state.vehicleSources, owned);
  }

  update(now: number) {
    this.active = true;
    this.reclaimSeat(now);
    this.sync();
    if (!this.client.state.connected || !this.client.state.you) return;
    for (const state of this.physics.latest?.vehicles ?? []) {
      if (!state.id || state.id === this.driving?.local || this.record(state.id) || this.pending.has(state.id) || now < (this.retryAt.get(state.id) ?? 0)) continue;
      if (state.id === 'player') {
        this.starterAt ??= { ...state.pos };
        if (Math.hypot(state.pos.x - this.starterAt.x, state.pos.z - this.starterAt.z) < .3
          && Math.hypot(state.linvel.x, state.linvel.z) < .3) continue;
      }
      const source = this.manifest.vehicles?.find(v => v.object_id === state.id);
      if (source && (this.client.state.vehicleSources.has(source.object_id)
        || Math.hypot(state.pos.x - source.x, state.pos.z - source.z) < 0.3)) continue;
      if (!source && !['player', 'scooter', 'truck'].includes(state.id)) continue;
      this.pending.add(state.id);
      const command = this.command(state, 'register');
      this.fresh.add(command.id);
      void this.client.vehicle(command).then(() => {
        this.pending.delete(state.id!); this.retryAt.set(state.id!, performance.now() + 10_000);
      });
    }
  }

  updates(now: number): VehicleUpdate[] {
    if (!this.client.state.connected) return [];
    const out: VehicleUpdate[] = [];
    for (const [id, item] of this.managed) {
      if (now < (this.blockedUntil.get(id) ?? 0)) continue;
      const v = this.client.state.vehicles.get(id), state = this.physics.latest?.vehicles.find(s => s.id === item.local);
      if (!v || v.owner !== this.client.state.you?.id || !state) continue;
      const pose = vehiclePose(state, v.pose.vehicle!, now);
      if (this.sent.get(id) !== vehicleFingerprint(pose)) out.push({ id, epoch: v.epoch, pose });
    }
    return out.slice(0, 8);
  }
  sentUpdates(updates: VehicleUpdate[]) { for (const v of updates) this.sent.set(v.id, vehicleFingerprint(v.pose)); }
}
