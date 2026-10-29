import type { V3 } from '../core/origin';
import type { PhysicsClient } from '../physics/client';
import { VEHICLES } from '../physics/constants';
import type { VehicleState } from '../physics/protocol';
import type { Manifest } from '../world/manifest';
import type { ParkedVehicles } from '../world/parkedVehicles';
import { yawOf } from '../gameplay/saveGame';
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

/** Exactly one local body per owned shared vehicle; replicas never enter Rapier's vehicle solver. */
export class SharedVehicles {
  private managed = new Map<string, { local: string; epoch: number }>();
  private pending = new Set<string>();
  private retryAt = new Map<string, number>();
  private sent = new Map<string, string>();
  private impulses = new Map<string, number>();
  private blockedUntil = new Map<string, number>();
  private active = false;
  private entering = false;

  constructor(readonly client: MultiplayerClient, private physics: PhysicsClient,
    private manifest: Manifest, private parked: ParkedVehicles) {
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
    return v.creator === this.client.state.you?.id ? v.localId : v.id;
  }
  record(localId: string | null): SharedVehicle | undefined {
    return [...this.client.state.vehicles.values()].find(v => this.localId(v) === localId);
  }
  hasSeat(localId: string): boolean { return this.record(localId)?.occupant === this.client.state.you?.id; }
  release(localId: string | null) {
    const v = this.record(localId);
    if (v && v.occupant === this.client.state.you?.id) void this.client.vehicle({ action: 'release', id: v.id });
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
      const result = await this.client.vehicle(existing ? { action: 'acquire', id: existing.id } : this.command(state!, 'acquire'));
      if (!result || !this.active) return null;
      this.sync();
      return this.localId(result);
    } finally { this.entering = false; }
  }

  nearest(at: V3): { id: string; distance: number } | null {
    const candidates = [...this.client.state.vehicles.values()].filter(v => !v.occupant)
      .filter(v => Math.abs(v.pose.H - at.y) < 3)
      .map(v => ({ id: this.localId(v), distance: Math.hypot(v.pose.E - at.x, -v.pose.N - at.z) }));
    return candidates.sort((a, b) => a.distance - b.distance)[0] ?? null;
  }

  sync() {
    const self = this.client.state.you?.id;
    if (!self || !this.active || !this.client.state.connected) return;
    const owned = new Set<string>();
    for (const v of this.client.state.vehicles.values()) {
      if (v.owner !== self) continue;
      if (v.source && this.localId(v) === v.source) owned.add(v.source);
      const local = this.localId(v), old = this.managed.get(v.id);
      if (old?.epoch === v.epoch) continue;
      const present = this.physics.latest?.vehicles.some(s => s.id === local);
      // Preserve the just-registered local body. A transferred/rejoined body uses
      // its exact last quaternion and velocity, including a flipped parked car.
      if (!present || old || v.creator !== self) this.physics.spawnVehicle(
        { x: v.pose.E, y: v.pose.H + VEHICLE_POSE_HEIGHT, z: -v.pose.N }, v.pose.yaw + Math.PI / 2,
        { id: local, kind: v.pose.mode as 'car' | 'scooter' | 'truck', halfExt: v.half, rot: v.pose.rot,
          linvel: { x: v.pose.vE, y: v.pose.vH, z: -v.pose.vN } });
      this.managed.set(v.id, { local, epoch: v.epoch }); this.sent.delete(v.id);
    }
    for (const [id, item] of this.managed) if (this.client.state.vehicles.get(id)?.owner !== self) {
      this.physics.removeVehicle(item.local); this.managed.delete(id); this.sent.delete(id);
    }
    this.parked.setShared(this.client.state.vehicleSources, owned);
  }

  update(now: number) {
    this.active = true;
    this.sync();
    if (!this.client.state.connected || !this.client.state.you) return;
    for (const state of this.physics.latest?.vehicles ?? []) {
      if (!state.id || this.record(state.id) || this.pending.has(state.id) || now < (this.retryAt.get(state.id) ?? 0)) continue;
      const source = this.manifest.vehicles?.find(v => v.object_id === state.id);
      if (source && (this.client.state.vehicleSources.has(source.object_id)
        || Math.hypot(state.pos.x - source.x, state.pos.z - source.z) < 0.3)) continue;
      if (!source && !['player', 'scooter', 'truck'].includes(state.id)) continue;
      this.pending.add(state.id);
      void this.client.vehicle(this.command(state, 'register')).then(() => {
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
      if (!v || !state) continue;
      const pose = vehiclePose(state, v.pose.vehicle!, now);
      if (this.sent.get(id) !== vehicleFingerprint(pose)) out.push({ id, epoch: v.epoch, pose });
    }
    return out.slice(0, 8);
  }
  sentUpdates(updates: VehicleUpdate[]) { for (const v of updates) this.sent.set(v.id, vehicleFingerprint(v.pose)); }
}
