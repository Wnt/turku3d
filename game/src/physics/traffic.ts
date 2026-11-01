// Ambient traffic inside the physics worker (street-richness plan §3.6). The network gives every car's position
// as a function of shared time; this class picks the cars this client shows (traffic/ring.ts), keeps permanent
// kinematic Rapier bodies on the ones near the player, and turns a car the player touches into an ordinary
// dynamic vehicle: a claim, which the main thread shares through net/sharedVehicles.ts so every client drops
// that slot.
import type * as RAPIER_NS from '@dimforge/rapier3d';
import type { V3 } from '../core/origin';
import { GROUP } from '../shared/col/materials';
import { CHASSIS_LIFT, MODEL_SIZE, MODEL_WEIGHTS, PAINT_WEIGHTS, RING, TRAFFIC, TRAFFIC_MODELS, TRAFFIC_PAINTS } from '../traffic/constants';
import { hash01, pick } from '../traffic/hash';
import { blankPose, TrafficNetwork, type CarPose } from '../traffic/network';
import { inView, mayCreate, mustRemove, type View } from '../traffic/ring';
import { trafficRoads } from '../traffic/roads';
import { buildTileTraffic, type Bounds, type Lane } from '../traffic/tileNetwork';
import type { RoadGraphJson } from '../gameplay/roadGraph';
import type { Quat, TrafficClaimEvent, TrafficConfig, TrafficTile } from './protocol';

type R = typeof RAPIER_NS;
const groups = (membership: number, filter: number) => ((membership & 0xffff) << 16) | (filter & 0xffff);
/** Numbers per car in the published flat array. */
export const TRAFFIC_STRIDE = 12;
/** Height samples along a lane (m). */
const SAMPLE_STEP = 4;
/** Lanes whose surface is sampled per physics step at most (raycasts are the expensive part). */
const SAMPLE_LANES_PER_STEP = 2;
/** Cars claimed without the player's car touching them (stopping for a pedestrian, a pile-up behind a claimed
 *  car) per minute, at most. Each claim is a registry vehicle in multiplayer (eight per player). */
const AUTO_CLAIMS_PER_MINUTE = 6;
/** Deceleration a car brakes with for a pedestrian ahead (m/s²), and the margin it stops short (m). */
const PEDESTRIAN_BRAKE = 3.5, PEDESTRIAN_MARGIN = 1.2;

export interface TrafficHost {
  /** Downward ray onto solid collision: surface Y or null. */
  ground(x: number, top: number, z: number, depth: number): number | null;
  /** Collision is resident under (x, z). */
  resident(x: number, z: number): boolean;
  spawnVehicle(pos: V3, yaw: number, spec: { id: string; kind: 'car'; halfExt: V3; rot: Quat; linvel: V3 }): void;
  /** What a rigid body is to traffic: the vehicle the local player is driving, a car this client claimed, any
   *  other vehicle (parked cars, which traffic passes rather than shoves), or something else. */
  bodyKind(handle: number): 'driven' | 'claimed' | 'vehicle' | null;
  /** Moves the walking character by (dx, dz) (a car it could not stop for pushes it aside). */
  pushCharacter(dx: number, dz: number): void;
  /** The walking character's feet, or null in a vehicle. */
  character(): V3 | null;
  /** Where the player is (feet or driven vehicle). */
  player(): V3 | null;
}

interface ActiveCar {
  handle: number;
  lane: Lane; n: number; key: string;
  model: number; paint: number;
  pose: CarPose;
  body: RAPIER_NS.RigidBody | null;
  visible: boolean;
}

export class TrafficSim {
  readonly network = new TrafficNetwork();
  private config: TrafficConfig = { cap: 0, offsetMs: 0, shared: false, enabled: null, claims: [] };
  private active = new Map<number, ActiveCar>();
  private byKey = new Map<string, ActiveCar>();
  private colliderCar = new Map<number, ActiveCar>();
  private nextHandle = 1;
  private localClaims = new Set<string>();
  private remoteClaims = new Set<string>();
  /** Claimed cars that crossed a seam carry new keys; cursors follow each claim's slot. */
  private cursors = new Map<string, { lane: Lane; n: number } | null>();
  private derived = new Set<string>();
  private heights = new Map<Lane, { ys: Float32Array; retry: number }>();
  private sampleQueue: Lane[] = [];
  private view: View | null = null;
  private lastActivation = -Infinity;
  private lastView: View | null = null;
  private fill = true;
  private pausedAt: number | null = null;
  private pauseOffset = 0;
  private autoClaims: number[] = [];
  private events: TrafficClaimEvent[] = [];
  /** Traffic time of the latest step (s since TRAFFIC.epochMs). */
  time = 0;
  stats = { active: 0, bodies: 0, lanes: 0, tiles: 0, stepMs: 0, claims: 0 };

  constructor(private readonly R: R, private readonly world: RAPIER_NS.World, private readonly host: TrafficHost) {}

  setTiles(add: TrafficTile[], remove: string[]) {
    for (const id of remove) this.network.removeTile(id);
    for (const t of add) {
      try {
        const bounds = t.bounds as Bounds;
        this.network.setTile(buildTileTraffic(t.tile, trafficRoads(t.roads as RoadGraphJson), bounds, t.hash));
      } catch (error) {
        // Unknown or malformed road data spawns nothing on that tile.
        this.network.removeTile(t.tile);
        console.warn(`[traffic] ${t.tile}: ${(error as Error).message}`);
      }
    }
    this.heights.clear(); this.sampleQueue = [];
    this.cursors.clear();
    this.stats.tiles = this.network.tiles.size;
    this.stats.lanes = [...this.network.tiles.values()].reduce((n, t) => n + t.lanes.length, 0);
    this.resync();
  }

  configure(change: Partial<TrafficConfig>) {
    if (change.enabled !== undefined) this.network.setEnabled(change.enabled ? new Set(change.enabled) : null);
    if (change.claims) { this.remoteClaims = new Set(change.claims); this.cursors.clear(); }
    this.config = { ...this.config, ...change };
    this.resync();
  }

  /** Collision changed in [x0, z0, x1, z1]: lanes there are sampled again (their old samples stay in use until then). */
  collisionChanged(area: readonly [number, number, number, number]) {
    for (const [lane, entry] of this.heights) {
      const b = lane.bbox;
      if (b[0] <= area[2] && b[2] >= area[0] && b[1] <= area[3] && b[3] >= area[1]) entry.retry = 0;
    }
  }

  setView(view: View | null) { this.view = view; }

  /** Shared traffic time for a wall-clock instant. Single player freezes it while paused; multiplayer never does. */
  clock(wallMs: number, running: boolean): number {
    if (!this.config.shared) {
      if (!running) { this.pausedAt ??= wallMs; }
      else if (this.pausedAt !== null) { this.pauseOffset += wallMs - this.pausedAt; this.pausedAt = null; }
    } else { this.pausedAt = null; this.pauseOffset = 0; }
    const wall = this.pausedAt ?? wallMs;
    return (wall - this.pauseOffset + this.config.offsetMs - TRAFFIC.epochMs) / 1000;
  }

  /** Before one fixed substep at traffic time t: ring, poses and kinematic targets. */
  beforeStep(t: number) {
    const t0 = performance.now();
    this.time = t;
    if (this.config.cap <= 0) { if (this.active.size) this.clear(); this.stats.stepMs = 0; return; }
    if (t - this.lastActivation >= RING.activationPeriod || t < this.lastActivation) { this.activate(t); this.lastActivation = t; }
    for (let i = 0; i < SAMPLE_LANES_PER_STEP && this.sampleQueue.length; i++) this.sample(this.sampleQueue.shift()!);
    const player = this.host.player();
    let bodies = 0;
    for (const car of this.active.values()) {
      const at = this.network.advance(car.lane, car.n, t);
      if (!at) { this.deactivate(car); continue; }
      if (at.lane !== car.lane) {
        car.lane = at.lane; car.n = at.n;
        const key = this.network.key(at.lane, at.n);
        if (key !== car.key) {
          this.byKey.delete(car.key); car.key = key;
          if (this.claimed(key) || this.byKey.has(key)) { this.deactivate(car); continue; }
          this.byKey.set(key, car);
        }
      } else car.n = at.n;
      this.network.pose(car.lane, car.n, t, this.height, car.pose);
      const near = player && Number.isFinite(car.pose.y)
        ? Math.hypot(car.pose.x - player.x, car.pose.z - player.z) < (car.body ? RING.bodyOff : RING.bodyOn) : false;
      if (near && !car.body) this.addBody(car);
      else if (!near && car.body) this.removeBody(car);
      if (car.body) {
        bodies++;
        car.body.setNextKinematicTranslation({ x: car.pose.x, y: car.pose.y + CHASSIS_LIFT, z: car.pose.z });
        car.body.setNextKinematicRotation(chassisRotation(car.pose));
      }
    }
    this.stats.active = this.active.size; this.stats.bodies = bodies;
    this.stats.stepMs = performance.now() - t0;
  }

  /** Rapier contact filter for traffic colliders: they push the player's car and pile up behind a claimed
   *  car, but pass parked cars instead of shoving them (positions cannot react to anything local). */
  readonly hooks: RAPIER_NS.PhysicsHooks = {
    filterContactPair: (c1, _c2, b1, b2) => {
      const other = this.colliderCar.has(c1) ? b2 : b1;
      const kind = other === undefined || other === null ? null : this.host.bodyKind(other);
      if (kind === 'vehicle' || (kind === 'claimed' && !this.autoBudget(false))) return null;
      return this.R.SolverFlags.COMPUTE_IMPULSE;
    },
    filterIntersectionPair: () => true,
  };

  /** After the solver step: contact with the player's car claims a car; so does a pedestrian in its braking
   *  path, or a car this client claimed ahead of it, within the automatic-claim budget. */
  afterStep(events: RAPIER_NS.EventQueue) {
    const hits = new Set<ActiveCar>();
    events.drainCollisionEvents((a, b, started) => {
      if (!started) return;
      const car = this.colliderCar.get(a) ?? this.colliderCar.get(b);
      if (!car || !car.body) return;
      const other = this.world.getCollider(this.colliderCar.has(a) ? b : a)?.parent();
      const kind = other ? this.host.bodyKind(other.handle) : null;
      if (kind === 'driven' || (kind === 'claimed' && this.autoBudget(true))) hits.add(car);
    });
    const feet = this.host.character();
    if (feet) for (const car of this.active.values()) {
      if (!car.body || hits.has(car)) continue;
      const contact = footprint(car, feet);
      if (contact.ahead && this.autoBudget(true)) hits.add(car);
      else if (contact.overlap) this.host.pushCharacter(contact.pushX, contact.pushZ);
    }
    for (const car of hits) this.claim(car.handle);
  }

  /** Converts an active car into a dynamic vehicle (contact, or the player entering it). Returns its id. */
  claim(handle: number): string | null {
    const car = this.active.get(handle);
    if (!car || !Number.isFinite(car.pose.y)) return null;
    const id = `tr:${car.key}`;
    const model = TRAFFIC_MODELS[car.model], [w, h, l] = MODEL_SIZE[model];
    const pos = { x: car.pose.x, y: car.pose.y + CHASSIS_LIFT, z: car.pose.z }, rot = chassisRotation(car.pose);
    const yaw = Math.atan2(-car.pose.hz, car.pose.hx);
    const linvel = { x: car.pose.hx * car.pose.speed, y: 0, z: car.pose.hz * car.pose.speed };
    this.localClaims.add(car.key);
    this.deactivate(car);
    this.host.spawnVehicle(pos, yaw, { id, kind: 'car', halfExt: { x: l / 2, y: h * 0.28, z: w / 2 }, rot, linvel });
    this.events.push({ revision: 0, handle, id, key: car.key, model, color: TRAFFIC_PAINTS[car.paint],
      size: [w, h, l], pos, rot, linvel });
    this.stats.claims++;
    return id;
  }

  /** Claim events of state revisions the main thread has not acknowledged: they repeat in every snapshot until
   *  then, so a skipped shared-mailbox frame cannot lose one. New events take the snapshot's `revision`. */
  pending(acknowledged: number, revision: number): TrafficClaimEvent[] {
    for (const e of this.events) if (!e.revision) e.revision = revision;
    this.events = this.events.filter(e => e.revision > acknowledged);
    return this.events;
  }

  /** Flat [handle, model, paint, x, y, z, qx, qy, qz, qw, vx, vz] per drawn car (chassis centre). */
  publish(): number[] {
    const out: number[] = [];
    for (const car of this.active.values()) {
      const p = car.pose;
      if (!Number.isFinite(p.y)) continue;
      const q = chassisRotation(p);
      out.push(car.handle, car.model, car.paint, round(p.x), round(p.y + CHASSIS_LIFT), round(p.z),
        round4(q.x), round4(q.y), round4(q.z), round4(q.w), round(p.hx * p.speed), round(p.hz * p.speed));
    }
    return out;
  }

  private claimed(key: string) { return this.localClaims.has(key) || this.remoteClaims.has(key) || this.derived.has(key); }

  /** Automatic claims left this minute; `use` spends one. */
  private autoBudget(use: boolean): boolean {
    const now = this.time;
    this.autoClaims = this.autoClaims.filter(t => now - t < 60 && t <= now);
    if (this.autoClaims.length >= AUTO_CLAIMS_PER_MINUTE) return false;
    if (use) this.autoClaims.push(now);
    return true;
  }

  /** Population ring at 5 Hz: removals, claim continuations across seams, then creations up to the cap. */
  private activate(t: number) {
    const view = this.view;
    if (!view) return;
    if (this.lastView && Math.hypot(view.x - this.lastView.x, view.z - this.lastView.z) > 150) this.fill = true;
    this.lastView = { ...view };
    this.followClaims(t);
    for (const car of this.active.values()) {
      if (!this.network.isEnabled(car.lane.tile) || this.claimed(car.key)) { this.deactivate(car); continue; }
      const d = Math.hypot(car.pose.x - view.x, car.pose.z - view.z);
      car.visible = inView(view, car.pose.x, car.pose.z);
      if (mustRemove(d, car.visible)) this.deactivate(car);
    }
    if (this.active.size >= this.config.cap) { this.fill = false; return; }
    const candidates: { lane: Lane; n: number; d: number; key: string }[] = [];
    this.network.enumerate(view.x, view.z, RING.createInViewMax, t, (lane, n, x, z) => {
      const key = this.network.key(lane, n);
      if (this.byKey.has(key) || this.claimed(key)) return;
      const d = Math.hypot(x - view.x, z - view.z);
      if (!mayCreate(d, inView(view, x, z), this.fill) || !this.host.resident(x, z)) return;
      candidates.push({ lane, n, d, key });
    });
    candidates.sort((a, b) => a.d - b.d || (a.key < b.key ? -1 : 1));
    for (const c of candidates) {
      if (this.active.size >= this.config.cap) break;
      if (this.byKey.has(c.key)) continue;
      const car: ActiveCar = { handle: this.nextHandle++, lane: c.lane, n: c.n, key: c.key,
        model: pick(hash01('model', c.key), MODEL_WEIGHTS), paint: pick(hash01('paint', c.key), PAINT_WEIGHTS),
        pose: blankPose(), body: null, visible: false };
      this.network.pose(car.lane, car.n, t, this.height, car.pose);
      this.active.set(car.handle, car); this.byKey.set(car.key, car);
    }
    this.fill = false;
  }

  /** Each claim's slot keeps moving: where it crosses into another tile it has a new key there, also claimed. */
  private followClaims(t: number) {
    this.derived.clear();
    for (const key of [...this.localClaims, ...this.remoteClaims]) {
      let cursor = this.cursors.get(key);
      if (cursor === undefined) {
        cursor = this.start(key);
        this.cursors.set(key, cursor);
      }
      if (!cursor) continue;
      // Walk forward along the chain; keys change only across seams.
      for (let guard = 0; guard < 256; guard++) {
        const next = this.network.continuation(cursor.lane, cursor.n);
        if (!next || next.lane.phase + next.n * TRAFFIC.headway > t) break;
        cursor = next;
        this.derived.add(this.network.key(cursor.lane, cursor.n));
      }
      this.cursors.set(key, cursor);
    }
  }

  /** The origin lane and slot of a claim key, when its tile is resident. */
  private start(key: string): { lane: Lane; n: number } | null {
    const [tile, origin, n0] = key.split(':');
    const lane = this.network.tiles.get(tile)?.byId.get(origin);
    const n = Number(n0);
    if (!lane || !Number.isInteger(n) || lane.cycle) return null;
    return { lane, n };
  }

  private deactivate(car: ActiveCar) {
    if (car.body) this.removeBody(car);
    this.active.delete(car.handle);
    if (this.byKey.get(car.key) === car) this.byKey.delete(car.key);
  }

  private clear() { for (const car of [...this.active.values()]) this.deactivate(car); }

  /** Lanes, gate, cap or claims changed: drop cars that no longer exist and let the ring refill. */
  private resync() {
    for (const car of [...this.active.values()]) {
      const tile = this.network.tiles.get(car.lane.tile);
      if (!tile || tile.lanes[car.lane.index] !== car.lane || !this.network.isEnabled(car.lane.tile) || this.claimed(car.key)) this.deactivate(car);
    }
    const view = this.view;
    const distance = (car: ActiveCar) => view ? Math.hypot(car.pose.x - view.x, car.pose.z - view.z) : 0;
    while (this.active.size > Math.max(0, this.config.cap)) {
      this.deactivate([...this.active.values()].sort((a, b) => distance(b) - distance(a))[0]);
    }
    this.lastActivation = -Infinity;
  }

  private addBody(car: ActiveCar) {
    const [w, h, l] = MODEL_SIZE[TRAFFIC_MODELS[car.model]];
    const body = this.world.createRigidBody(this.R.RigidBodyDesc.kinematicPositionBased()
      .setTranslation(car.pose.x, car.pose.y + CHASSIS_LIFT, car.pose.z).setRotation(chassisRotation(car.pose)));
    const collider = this.world.createCollider(this.R.ColliderDesc.cuboid(l / 2, h * 0.28, w / 2)
      .setCollisionGroups(groups(GROUP.VEHICLE, GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC))
      .setActiveEvents(this.R.ActiveEvents.COLLISION_EVENTS).setActiveHooks(this.R.ActiveHooks.FILTER_CONTACT_PAIRS), body);
    car.body = body;
    this.colliderCar.set(collider.handle, car);
  }

  private removeBody(car: ActiveCar) {
    if (!car.body) return;
    for (let i = 0; i < car.body.numColliders(); i++) this.colliderCar.delete(car.body.collider(i).handle);
    this.world.removeRigidBody(car.body);
    car.body = null;
  }

  /** Lane surface: the data's Y, else collision samples every 4 m (queued; null until sampled). */
  private height = (lane: Lane, s: number): number | null => {
    if (lane.ys) return interpolate(lane.ys, lane.cum, s);
    const entry = this.heights.get(lane);
    if (!entry || this.time > entry.retry) {
      if (!this.sampleQueue.includes(lane)) this.sampleQueue.push(lane);
      if (!entry) return null;
    }
    const samples = entry.ys;
    const f = Math.max(0, Math.min(samples.length - 1, s / SAMPLE_STEP)), i = Math.min(samples.length - 2, Math.floor(f));
    if (samples.length === 1) return Number.isFinite(samples[0]) ? samples[0] : null;
    const a = samples[i], b = samples[i + 1];
    const y = a + (b - a) * (f - i);
    return Number.isFinite(y) ? y : Number.isFinite(a) ? a : Number.isFinite(b) ? b : null;
  };

  /** Samples a lane's road surface, following it under bridges by searching near the previous sample. */
  private sample(lane: Lane) {
    const count = Math.max(2, Math.ceil(lane.length / SAMPLE_STEP) + 1);
    const out = new Float32Array(count).fill(NaN);
    let previous: number | null = null;
    for (let i = 0; i < count; i++) {
      const s = Math.min(lane.length, i * SAMPLE_STEP);
      const p = pointAlong(lane, s);
      let y: number | null = null;
      if (this.host.resident(p.x, p.z)) {
        y = previous !== null ? this.host.ground(p.x, previous + 3, p.z, 8) : null;
        y ??= this.host.ground(p.x, 400, p.z, 500);
      }
      if (y !== null) { out[i] = y; previous = y; }
    }
    // A lane with unresident samples is sampled again later, once its collision arrives.
    this.heights.set(lane, { ys: out, retry: out.some(v => !Number.isFinite(v)) ? this.time + 2 : Infinity });
  }
}

/** Physics chassis rotation (+X forward): heading yaw about Y, then pitch about the chassis Z axis. */
export function chassisRotation(p: CarPose): Quat {
  const yaw = Math.atan2(-p.hz, p.hx);
  const sy = Math.sin(yaw / 2), cy = Math.cos(yaw / 2), sp = Math.sin(p.pitch / 2), cp = Math.cos(p.pitch / 2);
  return { x: sy * sp, y: sy * cp, z: cy * sp, w: cy * cp };
}

/** The character against a car's footprint (capsule radius included): inside the braking path ahead of it, or
 *  overlapping it, with the shortest sideways push out. */
export function footprint(car: { model: number; pose: CarPose }, feet: V3) {
  const [w, h, l] = MODEL_SIZE[TRAFFIC_MODELS[car.model]];
  const p = car.pose, dx = feet.x - p.x, dz = feet.z - p.z;
  const along = dx * p.hx + dz * p.hz, across = -dx * p.hz + dz * p.hx;
  const side = w / 2 + 0.35, half = l / 2 + 0.35;
  const level = Math.abs(feet.y - p.y) < h + 0.5;
  const stop = (p.speed * p.speed) / (2 * PEDESTRIAN_BRAKE) + PEDESTRIAN_MARGIN;
  const overlap = level && Math.abs(along) < half && Math.abs(across) < side;
  const ahead = level && p.speed > 0.5 && along > -half && along < half + stop && Math.abs(across) < side + 0.1;
  const push = overlap ? (across >= 0 ? side - across : -side - across) : 0;
  return { overlap, ahead, pushX: -p.hz * push, pushZ: p.hx * push };
}

function pointAlong(lane: Lane, s: number) {
  const p = lane.points, cum = lane.cum, n = cum.length;
  let i = 1;
  while (i < n - 1 && cum[i] < s) i++;
  const span = cum[i] - cum[i - 1] || 1, f = Math.min(1, Math.max(0, (s - cum[i - 1]) / span));
  return { x: p[2 * i - 2] + (p[2 * i] - p[2 * i - 2]) * f, z: p[2 * i - 1] + (p[2 * i + 1] - p[2 * i - 1]) * f };
}

function interpolate(ys: Float64Array, cum: Float64Array, s: number): number {
  const n = cum.length;
  let i = 1;
  while (i < n - 1 && cum[i] < s) i++;
  const span = cum[i] - cum[i - 1] || 1, f = Math.min(1, Math.max(0, (s - cum[i - 1]) / span));
  return ys[i - 1] + (ys[i] - ys[i - 1]) * f;
}

const round = (v: number) => Math.round(v * 1000) / 1000;
const round4 = (v: number) => Math.round(v * 10000) / 10000;
