// Main-thread side of ambient traffic: feeds the physics worker the resident tiles' road graphs, the shared
// clock, the multiplayer gate and remote claims; copies the worker's car poses into the shared per-body
// InstancedMeshes (actors/car.ts, +0 draws); turns the worker's claim events into ordinary vehicle records
// that parked cars, saves and net/sharedVehicles.ts already know how to handle.
import * as THREE from 'three/webgpu';
import type { ParkedCarVisuals, TrafficInstance } from '../actors/car';
import type { V3 } from '../core/origin';
import type { RoadGraphJson } from '../gameplay/roadGraph';
import type { PhysicsClient } from '../physics/client';
import type { PhysicsState, TrafficClaimEvent, TrafficClock, TrafficStats } from '../physics/protocol';
import { tileBounds } from '../render/lights/tileLights';
import type { Entry } from '../world/coarseContracts';
import type { Manifest, ParkedVehicle } from '../world/manifest';
import type { ParkedVehicles } from '../world/parkedVehicles';
import { DAY_MS } from '../core/dayCycle';
import { CHASSIS_LIFT, CLAIM_PREFIX, TRAFFIC_MODELS, TRAFFIC_PAINTS } from './constants';
import { enabledTiles, roadHash } from './gate';

const STRIDE = 12;
const SYNC_MS = 250;
/** Entering a moving car: the claim must arrive within this long. */
const CLAIM_TIMEOUT_MS = 1500;

export interface TrafficMultiplayer {
  /** Server clock minus local wall clock (ms). */
  offsetMs: number;
  /** Server-agreed road hash per tile, or null before the server answered (or when it cannot). */
  agreed: ReadonlyMap<string, string> | null;
  /** Registry sources of claimed traffic slots (`tr:<key>`). */
  sources: Iterable<string>;
}

interface Drawn { handle: number; model: number; paint: number; x: number; y: number; z: number; q: { x: number; y: number; z: number; w: number }; vx: number; vz: number }

export class TrafficClient {
  private source: { entries: ReadonlyMap<string, Entry> } | null = null;
  private hashes = new WeakMap<RoadGraphJson, string>();
  /** Tiles sent to the worker, with their hash. */
  readonly local = new Map<string, string>();
  private nextSync = 0;
  private sent = { enabled: '', claims: '', offset: NaN, shared: false, clock: '' };
  private multiplayer: TrafficMultiplayer | null = null;
  private handled = new Set<string>();
  private claims = new Map<string, TrafficClaimEvent>();
  private waiters = new Map<number, (id: string | null) => void>();
  private drawn: Drawn[] = [];
  private instances: TrafficInstance[] = [];
  /** Changes whenever the local tile hashes change (multiplayer reports them). */
  revision = 0;
  stats: TrafficStats | null = null;

  /** `density` multiplies every street's flow (traffic/density.ts); `clock` is the clock single-player density
   *  follows (multiplayer always follows the shared cycle). */
  constructor(private readonly physics: PhysicsClient, private readonly visuals: ParkedCarVisuals,
    private readonly parked: Pick<ParkedVehicles, 'adopt'>, private readonly manifest: Manifest, readonly cap: number,
    readonly density = 1, private readonly clock: () => TrafficClock = () => ({ kind: 'cycle', periodMs: DAY_MS })) {
    visuals.reserveTraffic(cap);
    // A hot-swapped session restarts from a clean worker state.
    physics.trafficTiles([], [...physics.trafficResident]);
    physics.trafficResident.clear();
    const start = clock();
    this.sent.clock = JSON.stringify(start);
    physics.traffic({ cap, offsetMs: 0, shared: false, enabled: null, claims: [], density, clock: start });
  }

  /** Follow the drawn draft tiles' road graphs (CoarseTiles). */
  attach(source: { entries: ReadonlyMap<string, Entry> } | null) { this.source = source; this.nextSync = 0; }

  /** Multiplayer clock, gate and claims; null in single player. */
  setMultiplayer(mp: TrafficMultiplayer | null) { this.multiplayer = mp; }

  /** Per frame, after the physics state was read and before the parked visuals update. */
  update(now: number, camera: THREE.PerspectiveCamera, state: PhysicsState | null) {
    const t0 = performance.now();
    this.updateNow(now, camera, state);
    this.mainMs += (performance.now() - t0 - this.mainMs) * 0.05;
  }
  /** Main-thread cost of update() (ms, EMA); the matrix copy happens in the parked visuals' update. */
  mainMs = 0;

  private updateNow(now: number, camera: THREE.PerspectiveCamera, state: PhysicsState | null) {
    if (now >= this.nextSync) { this.nextSync = now + SYNC_MS; this.syncTiles(); this.syncConfig(); }
    const forward = camera.getWorldDirection(scratch);
    const length = Math.hypot(forward.x, forward.z) || 1;
    this.physics.trafficView = { x: camera.position.x, z: camera.position.z, fx: forward.x / length, fz: forward.z / length,
      halfFov: Math.atan(Math.tan((camera.fov * Math.PI) / 360) * camera.aspect) };
    if (!state) return;
    this.stats = state.trafficStats ?? null;
    if (state.trafficTime !== undefined) this.time = state.trafficTime + (state.publishedAt === undefined ? 0
      : Math.min(0.1, Math.max(0, (performance.timeOrigin + performance.now() - state.publishedAt) / 1000)));
    for (const event of state.trafficEvents ?? []) this.claimed(event);
    this.read(state);
    this.visuals.setTraffic(this.instances);
  }

  /** The nearest drawn moving car within `radius` of a walking player (for entering it). */
  nearest(at: V3, radius = 4): { handle: number; distance: number } | null {
    let best: { handle: number; distance: number } | null = null;
    for (const car of this.drawn) {
      if (Math.abs(car.y - CHASSIS_LIFT - at.y) > 3) continue;
      const distance = Math.hypot(car.x - at.x, car.z - at.z);
      if (distance < radius && (!best || distance < best.distance)) best = { handle: car.handle, distance };
    }
    return best;
  }

  /** The audio mix's view of traffic: the nearest moving cars within 120 m (at most `voices`) and the count and
   *  centre of those within 150 m. */
  audible(at: V3, voices: number) {
    const near: { id: number; position: V3; speed: number; d: number }[] = [];
    let count = 0, cx = 0, cz = 0;
    for (const car of this.drawn) {
      const d = Math.hypot(car.x - at.x, car.z - at.z);
      if (d > 150) continue;
      count++; cx += car.x; cz += car.z;
      if (d <= 120) near.push({ id: car.handle, position: { x: car.x, y: car.y, z: car.z }, speed: Math.hypot(car.vx, car.vz), d });
    }
    near.sort((a, b) => a.d - b.d);
    return { cars: near.slice(0, voices).map(({ id, position, speed }) => ({ id, position, speed })), count,
      centre: count ? { x: cx / count, y: at.y, z: cz / count } : null };
  }

  /** Claims a moving car for the player to enter; resolves with its vehicle id, or null if it is gone. */
  enter(handle: number): Promise<string | null> {
    return new Promise(resolve => {
      const timer = setTimeout(() => { this.waiters.delete(handle); resolve(null); }, CLAIM_TIMEOUT_MS);
      this.waiters.set(handle, id => { clearTimeout(timer); resolve(id); });
      this.physics.trafficClaim(handle);
    });
  }

  /** Scorecard / QA view: drawn moving cars (engine x, z), cap and worker stats. */
  snapshot() {
    return { cap: this.cap, density: this.density, hour: this.stats?.hour ?? null, drawn: this.drawn.length, mainMs: this.mainMs, tiles: this.local.size,
      enabled: this.sent.enabled === '*' ? 'all' : this.sent.enabled.split(',').filter(Boolean).length, time: this.time,
      claims: this.claims.size, moving_cars: this.drawn.map(c => [Math.round(c.x * 10) / 10, Math.round(c.z * 10) / 10]), stats: this.stats };
  }

  /** Drawn cars with handle and velocity at traffic time `time` (QA: comparing clients). */
  debugCars() { return { time: this.time, cars: this.drawn.map(c => ({ handle: c.handle, x: c.x, z: c.z, vx: c.vx, vz: c.vz })) }; }
  /** Traffic time (s) of the latest worker state, moved on to this frame like the drawn poses. */
  private time = 0;

  private syncTiles() {
    if (!this.source) return;
    const add: { tile: string; hash: string; bounds: [number, number, number, number]; roads: unknown }[] = [];
    const seen = new Set<string>();
    for (const [tile, e] of this.source.entries) {
      if (e.state !== 'draft' || !e.roads) continue;
      const bounds = tileBounds(tile);
      if (!bounds) continue;
      seen.add(tile);
      let hash = this.hashes.get(e.roads);
      if (!hash) { hash = roadHash(e.roads); this.hashes.set(e.roads, hash); }
      if (this.local.get(tile) === hash) continue;
      this.local.set(tile, hash);
      add.push({ tile, hash, bounds: [...bounds] as [number, number, number, number], roads: e.roads });
    }
    const remove = [...this.local.keys()].filter(tile => !seen.has(tile));
    for (const tile of remove) this.local.delete(tile);
    if (!add.length && !remove.length) return;
    for (const t of add) this.physics.trafficResident.add(t.tile);
    for (const t of remove) this.physics.trafficResident.delete(t);
    this.physics.trafficTiles(add, remove);
    this.revision++;
  }

  private syncConfig() {
    const mp = this.multiplayer, shared = !!mp;
    const enabled = enabledTiles(this.local, mp?.agreed ?? null, shared);
    const claims = mp ? [...mp.sources].filter(s => s.startsWith(CLAIM_PREFIX)).map(s => s.slice(CLAIM_PREFIX.length)).sort() : [];
    const offset = mp ? Math.round(mp.offsetMs) : 0;
    const change: Parameters<PhysicsClient['traffic']>[0] = {};
    const enabledKey = enabled === null ? '*' : enabled.join(','), claimsKey = claims.join(',');
    if (enabledKey !== this.sent.enabled) { change.enabled = enabled; this.sent.enabled = enabledKey; }
    if (claimsKey !== this.sent.claims) { change.claims = claims; this.sent.claims = claimsKey; }
    if (shared !== this.sent.shared) { change.shared = shared; this.sent.shared = shared; }
    if (!(Math.abs(offset - this.sent.offset) < 20)) { change.offsetMs = offset; this.sent.offset = offset; }
    // A frozen hour is sent to the minute: the developer menu and ?time= move it, a running clock never does.
    const clock = this.clock(), clockKey = JSON.stringify(clock.kind === 'hour' ? { ...clock, hour: Math.round(clock.hour * 60) / 60 } : clock);
    if (clockKey !== this.sent.clock) { change.clock = JSON.parse(clockKey) as TrafficClock; this.sent.clock = clockKey; }
    if (Object.keys(change).length) this.physics.traffic(change);
  }

  /** Drawn cars from the worker, moved on by their velocity to this frame (the worker publishes at 60 Hz). */
  private read(state: PhysicsState) {
    const flat = state.traffic ?? [];
    const dt = state.publishedAt === undefined ? 0
      : Math.min(0.1, Math.max(0, (performance.timeOrigin + performance.now() - state.publishedAt) / 1000));
    const count = flat.length / STRIDE;
    this.drawn.length = count;
    this.instances.length = 0;
    for (let i = 0; i < count; i++) {
      const o = i * STRIDE;
      const car = this.drawn[i] ??= { handle: 0, model: 0, paint: 0, x: 0, y: 0, z: 0, q: { x: 0, y: 0, z: 0, w: 1 }, vx: 0, vz: 0 };
      car.handle = flat[o]; car.model = flat[o + 1]; car.paint = flat[o + 2];
      car.vx = flat[o + 10]; car.vz = flat[o + 11];
      car.x = flat[o + 3] + car.vx * dt; car.y = flat[o + 4]; car.z = flat[o + 5] + car.vz * dt;
      car.q.x = flat[o + 6]; car.q.y = flat[o + 7]; car.q.z = flat[o + 8]; car.q.w = flat[o + 9];
      this.instances.push({ model: TRAFFIC_MODELS[car.model], color: TRAFFIC_PAINTS[car.paint], x: car.x, y: car.y, z: car.z, q: car.q });
    }
    // A just-claimed car is drawn here from its physics body until the parked batches include its record.
    for (const [id, claim] of this.claims) {
      if (this.visuals.renders(id)) { this.claims.delete(id); continue; }
      const body = state.vehicles.find(v => v.id === id);
      if (body) this.instances.push({ model: claim.model, color: claim.color, x: body.pos.x, y: body.pos.y, z: body.pos.z, q: body.rot });
    }
  }

  /** A claim from the worker: a vehicle record (like a draft's parked car) that the parked cars manage. */
  private claimed(event: TrafficClaimEvent) {
    if (this.handled.has(event.id)) return;
    this.handled.add(event.id);
    const [width, height, length] = event.size;
    const yaw = 2 * Math.atan2(event.rot.y, event.rot.w) + Math.PI / 2;
    const record: ParkedVehicle = { object_id: event.id, model: event.model, color: event.color,
      x: event.pos.x, y: event.pos.y - CHASSIS_LIFT, z: event.pos.z, yaw, width, length, height,
      clear_front_m: 10, clear_rear_m: 10 };
    this.manifest.vehicles = [...(this.manifest.vehicles ?? []).filter(v => v.object_id !== event.id), record];
    this.parked.adopt(event.id, record);
    this.claims.set(event.id, event);
    if (event.handle !== undefined) { this.waiters.get(event.handle)?.(event.id); this.waiters.delete(event.handle); }
  }
}

const scratch = new THREE.Vector3();
