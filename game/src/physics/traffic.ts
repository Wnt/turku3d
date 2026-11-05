import {fleetWheelFootprint} from '../actors/fleet/wheelLayouts';
import { sampleWheelGround, wheelGroundPose, type WheelGroundPose } from '../vehicle/wheelGround';
import { fleetModel, type FleetModel } from '../actors/fleet/catalog';
// Ambient traffic inside the physics worker (street-richness plan §3.6). The network gives every car's position
// as a function of shared time; this class picks the cars this client shows (traffic/ring.ts), keeps permanent
// kinematic Rapier bodies on the ones near the player, and turns a car the player touches into an ordinary
// dynamic vehicle: a claim, which the main thread shares through net/sharedVehicles.ts so every client drops
// that slot.
//
// Density (traffic/density.ts) only decides which slots carry cars. The ring creates cars only on such slots; a
// drawn car whose slot stops carrying one (its chain moved onto a quieter street, a seam whose neighbour hashed
// it out, a density or clock change) keeps driving until it is out of view or beyond the in-view creation
// minimum, so no car vanishes closer than one may appear.
import type * as RAPIER_NS from '@dimforge/rapier3d';
import { DAY_MS, cycleHour } from '../core/dayCycle';
import type { V3 } from '../core/origin';
import { GROUP } from '../shared/col/materials';
import { CHASSIS_LIFT, MODEL_SIZE, PAINT_WEIGHTS, RING, TRAFFIC, TRAFFIC_MODELS, TRAFFIC_PAINTS, TRAFFIC_STRIDE } from '../traffic/constants';
import { hash01, hash32, pick } from '../traffic/hash';
import { blankPose, TrafficNetwork, type CarPose } from '../traffic/network';
import { FLOW_STEP, TrafficFlow, interpolateFlowPose, type FlowCar } from '../traffic/flow';
import { honking, vehicleLights } from '../traffic/lights';
import { inView, mayCreate, mustRemove, type View } from '../traffic/ring';
import { trafficRoads } from '../traffic/roads';
import { ClaimSet } from '../traffic/claims';
import { buildTileTraffic, type Bounds, type Lane } from '../traffic/tileNetwork';
import type { RoadGraphJson } from '../gameplay/roadGraph';
import type { TrafficPedestrian, TrafficVehicle } from '../traffic/pedestrians';
import type { Quat, TrafficClaimEvent, TrafficConfig, TrafficTile } from './protocol';

type R = typeof RAPIER_NS;
const groups = (membership: number, filter: number) => ((membership & 0xffff) << 16) | (filter & 0xffff);
/** Numbers per car in the published flat array. */
export { TRAFFIC_STRIDE };
/** Height samples along a lane (m). */
const SAMPLE_STEP = 4;
/** Worker time per physics step for sampling lane surfaces (ms): a long lane is sampled over several steps.
 *  A ray from far above costs much more than one near the previous sample, so the budget is time, not rays. */
const SAMPLE_MS_PER_STEP = 0.2;
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
  /** Every player this client knows of, on foot and in vehicles (local and remote): drivers stop for them. */
  players(): { people: TrafficPedestrian[]; vehicles: TrafficVehicle[] };
  /** Where the player is (feet or driven vehicle). */
  player(): V3 | null;
}

/** GTA-style car tiers (techstudy Brief 1; live GTA V, REPORT §7 L4: real physics for about five cars, the nearest
 *  moving ones within 11-35 m; parked and waiting cars are dummies). Real cars (moving, in view, within 35 m and
 *  among the 5 nearest such; kept to 40 m and the 6 nearest) sample their wheels on collision every substep. A car
 *  stopped for half a second is a dummy. Dummies follow their lane's collision-sampled heights and pitch with no
 *  wheel rays. Far cars (beyond 200 m, back inside 190 m) take an exact pose at 5 Hz and drift along their heading
 *  in between. A real/dummy change blends wheel and lane poses over half a second. Rendering only: the shared flow,
 *  identities and bodies (within 70 m of the player) are untouched. */
export const TIERS = { realIn: 35, realOut: 40, realCount: 5, realKeep: 6, stopped: 0.5, farIn: 200, farOut: 190, farPeriod: 0.2, blend: 0.5 } as const;
export type Tier = 0 | 1 | 2;

interface ActiveCar {
  motion: FlowCar;
  /** 0 real, 1 dummy, 2 far; `blend` runs 0 (lane pose) to 1 (wheel pose). */
  tier: Tier; blend: number;
  /** Far tier: the time and position of the last exact pose. */
  farAt: number; farX: number; farZ: number;
  /** Drawn chassis rotation and wheel heights (blended between lane and wheel poses). */
  rot: Quat; wheelY: number[];
  distance: number;
  /** Appearance seed, hashed once. */
  seed?: number;
  handle: number;
  lane: Lane; n: number; key: string;
  model: number; paint: number;
  pose: CarPose;
  body: RAPIER_NS.RigidBody | null;
  visible: boolean;
  terrain?: WheelGroundPose;
  visualModel?: FleetModel;
  groundAt?: {x:number;z:number;yaw:number};
}

/** Wall time (ms) a physics step may spend on a traffic graph rebuild. */
const REBUILD_MS_PER_STEP = 3;

export class TrafficSim {
  readonly network = new TrafficNetwork();
  readonly flow = new TrafficFlow(this.network);
  private config: TrafficConfig = { cap: 0, offsetMs: 0, shared: false, enabled: null, claims: [], density: 1,
    clock: { kind: 'cycle', periodMs: DAY_MS } };
  private active = new Map<number, ActiveCar>();
  private byKey = new Map<string, ActiveCar>();
  private colliderCar = new Map<number, ActiveCar>();
  private nextHandle = 1;
  readonly claims = new ClaimSet(this.network);
  private heights = new Map<Lane, { ys: Float32Array; retry: number }>();
  private sampleQueue: Lane[] = [];
  private sampling: { lane: Lane; out: Float32Array; i: number; previous: number | null } | null = null;
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
  /** `stepMs`: mean traffic cost per physics substep (ring, poses, bodies, contacts; ms, EMA), `stepMax` its
   *  recent peak (decaying), `activationMs` the last ring pass. */
  stats = { active: 0, bodies: 0, lanes: 0, tiles: 0, stepMs: 0, stepMax: 0, activationMs: 0, claims: 0, hour: 0, deferred: 0,
    /** Drawn cars per tier: real, dummy, far. */
    tiers: [0, 0, 0] };
  private substepMs = 0;
  private previousTime = 0;
  private ground = (x: number, top: number, z: number, depth: number) => this.host.ground(x, top, z, depth);
  /** Lanes waiting for (or being) sampled. */
  private queued = new Set<Lane>();

  constructor(private readonly R: R, private readonly world: RAPIER_NS.World, private readonly host: TrafficHost) {
    this.network.density = { hourAt: t => this.hourAt(t), multiplier: 1 };
    // A tile install rebuilds the traffic graph over several steps instead of stalling one (worker step budget).
    this.flow.rebuildBudgetMs = REBUILD_MS_PER_STEP;
  }

  /** Clock hour at traffic time t. Multiplayer: the shared cycle, so every client thins alike. Single player: the
   *  clock the player sees, a frozen hour or the (possibly rescaled) cycle at the wall time t corresponds to. */
  hourAt(t: number): number {
    const clock = this.config.shared ? { kind: 'cycle' as const, periodMs: DAY_MS } : this.config.clock;
    return clock.kind === 'hour' ? clock.hour : cycleHour(TRAFFIC.epochMs + t * 1000 + this.pauseOffset, clock.periodMs).hour;
  }

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
    this.heights.clear(); this.sampleQueue = []; this.sampling = null; this.queued.clear();
    this.claims.reset();
    this.stats.tiles = this.network.tiles.size;
    this.stats.lanes = [...this.network.tiles.values()].reduce((n, t) => n + t.lanes.length, 0);
    this.resync();
  }

  configure(change: Partial<TrafficConfig>) {
    if (change.enabled !== undefined) this.network.setEnabled(change.enabled ? new Set(change.enabled) : null);
    if (change.claims) { this.claims.setRemote(change.claims); for (const key of change.claims) this.flow.suppress(key); }
    if (change.watch) {
      // Every client turns the same stamped list into the same tick window and engine positions (x = E, z = -N).
      const from = Math.ceil((change.watch.at + TRAFFIC.watchDelayMs - TRAFFIC.epochMs) / 1000 / FLOW_STEP - 1e-9);
      this.flow.scheduleWatch(from, from + Math.round(TRAFFIC.watchValidMs / 1000 / FLOW_STEP),
        change.watch.points.map(([E, N]) => ({ x: E, z: -N })));
    }
    this.config = { ...this.config, ...change };
    this.network.density!.multiplier = Math.max(0, this.config.density);
    this.resync();
  }

  /** Collision changed in [x0, z0, x1, z1]: lanes there are sampled again (their old samples stay in use until then). */
  collisionChanged(area: readonly [number, number, number, number]) {
    for(const car of this.active.values())if(car.pose.x>=area[0]&&car.pose.x<=area[2]&&car.pose.z>=area[1]&&car.pose.z<=area[3])car.groundAt=undefined;
    for (const [lane, entry] of this.heights) {
      const b = lane.bbox;
      if (b[0] <= area[2] && b[2] >= area[0] && b[1] <= area[3] && b[3] >= area[1]) entry.retry = 0;
    }
  }

  /** The camera; in single player also the flow's watcher for out-of-view recycling (its 90 m all-round radius covers
   *  the player it follows). Multiplayer recycles only on the server-stamped watch (configure `watch`). */
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
    const players = this.host.players();
    this.flow.setPlayers(players.people, players.vehicles, t);
    if (!this.config.shared) this.flow.setWatchers(this.view ? [{ ...this.view }] : null);
    this.flow.update(t);
    if (this.config.cap <= 0) { if (this.active.size) this.clear(); this.substepMs = 0; this.stats.active = this.stats.bodies = 0; return; }
    if (t - this.lastActivation >= RING.activationPeriod || t < this.lastActivation) {
      const a0 = performance.now();
      this.activate(t); this.lastActivation = t;
      this.stats.activationMs = performance.now() - a0;
    }
    this.sampleSome(performance.now() + SAMPLE_MS_PER_STEP);
    const player = this.host.player();
    const step = Math.max(0, Math.min(0.1, t - this.previousTime));
    this.previousTime = t;
    let bodies = 0;
    for (const car of this.active.values()) {
      const motion = this.flow.cars.get(car.key);
      if (!motion) { this.deactivate(car); continue; }
      car.motion = motion; car.lane = motion.lane; car.n = motion.n;
      if (car.tier !== 2 || t - car.farAt >= TIERS.farPeriod || t < car.farAt) {
        interpolateFlowPose(motion, (t - this.flow.tick * FLOW_STEP) / FLOW_STEP, this.height, car.pose);
        car.farAt = t; car.farX = car.pose.x; car.farZ = car.pose.z;
      } else {
        // Far: closed-form drift along the last exact heading until the next 5 Hz pose.
        const dt = t - car.farAt;
        car.pose.x = car.farX + car.pose.hx * car.pose.speed * dt; car.pose.z = car.farZ + car.pose.hz * car.pose.speed * dt;
      }
      if (this.view) {
        const vx = car.pose.x - this.view.x, vz = car.pose.z - this.view.z;
        if (mustRemove(Math.sqrt(vx * vx + vz * vz), inView(this.view, car.pose.x, car.pose.z))) { this.deactivate(car); continue; }
      }
      // Real cars sample their wheels on collision; a tier change blends towards the other pose over half a second.
      car.blend = Math.max(0, Math.min(1, car.blend + (car.tier === 0 ? step : -step) / TIERS.blend));
      if(car.blend>0&&Number.isFinite(car.pose.y)&&this.host.resident(car.pose.x,car.pose.z)){
        const p=car.pose,yaw=Math.atan2(-p.hz,p.hx),last=car.groundAt;
        if(!last||(p.x-last.x)**2+(p.z-last.z)**2>.0064||Math.abs(yaw-last.yaw)>.025){
          const model=car.visualModel??=fleetModel(TRAFFIC_MODELS[car.model],String(hash32('appearance',car.key)));
          const pose=sampleWheelGround({x:p.x,y:p.y,z:p.z},yaw,fleetWheelFootprint(model),this.ground);
          car.terrain=pose??undefined;
          if(last){last.x=p.x;last.z=p.z;last.yaw=yaw;}else car.groundAt={x:p.x,z:p.z,yaw};
        }
      }else car.terrain=undefined;
      chassisRotationInto(car.pose, car.rot);
      const rest = (car.visualModel?.wheel ?? .33) - CHASSIS_LIFT;
      if(car.terrain){
        const model=car.visualModel!;
        car.terrain=wheelGroundPose({x:car.pose.x,y:car.pose.y,z:car.pose.z},Math.atan2(-car.pose.hz,car.pose.hx),
          fleetWheelFootprint(model),car.terrain.heights)!;
        const b = car.blend, wheelY = car.terrain.pos.y - CHASSIS_LIFT;
        car.pose.y = b >= 1 || !Number.isFinite(car.pose.y) ? wheelY : car.pose.y + (wheelY - car.pose.y) * b;
        blendRotation(car.rot, car.terrain.rot, b >= 1 || !Number.isFinite(car.pose.y) ? 1 : b);
        for (let i = 0; i < 4; i++) car.wheelY[i] = rest + (car.terrain.wheelY[i] - rest) * b;
      } else for (let i = 0; i < 4; i++) car.wheelY[i] = rest;
      const near = player && Number.isFinite(car.pose.y)
        ? Math.hypot(car.pose.x - player.x, car.pose.z - player.z) < (car.body ? RING.bodyOff : RING.bodyOn) : false;
      if (near && !car.body) this.addBody(car);
      else if (!near && car.body) this.removeBody(car);
      if (car.body) {
        bodies++;
        car.body.setNextKinematicTranslation({ x: car.pose.x, y: car.pose.y + CHASSIS_LIFT, z: car.pose.z });
        car.body.setNextKinematicRotation(car.rot);
      }
    }
    this.stats.active = this.active.size; this.stats.bodies = bodies;
    this.substepMs = performance.now() - t0;
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
    const t0 = performance.now();
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
    const ms = this.substepMs + performance.now() - t0;
    this.stats.stepMs += (ms - this.stats.stepMs) * 0.05;
    this.stats.stepMax = Math.max(ms, this.stats.stepMax * 0.995);
  }

  /** Converts an active car into a dynamic vehicle (contact, or the player entering it). Returns its id. */
  claim(handle: number): string | null {
    const car = this.active.get(handle);
    if (!car || !Number.isFinite(car.pose.y)) return null;
    const id = `tr:${car.key}`;
    const model = TRAFFIC_MODELS[car.model], [w, h, l] = MODEL_SIZE[model];
    const pos = { x: car.pose.x, y: car.pose.y + CHASSIS_LIFT, z: car.pose.z }, rot = { ...car.rot };
    const yaw = Math.atan2(-car.pose.hz, car.pose.hx);
    const linvel = { x: car.pose.hx * car.pose.speed, y: 0, z: car.pose.hz * car.pose.speed };
    this.claims.addLocal(car.key);
    this.flow.suppress(car.key);
    this.deactivate(car);
    this.host.spawnVehicle(pos, yaw, { id, kind: 'car', halfExt: { x: l / 2, y: h * 0.28, z: w / 2 }, rot, linvel });
    this.events.push({ revision: 0, handle, id, key: car.key, seed: String(hash32('appearance', car.key)), model, color: TRAFFIC_PAINTS[car.paint],
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

  /** Identities of the drawn cars (QA and tests). */
  activeKeys(): string[] { return [...this.byKey.keys()].sort(); }
  /** The drawn cars with their identity and ground position (QA and tests). */
  activeCars() { return [...this.active.values()].map(c => ({ key: c.key, handle: c.handle, x: c.pose.x, z: c.pose.z, body: !!c.body })); }

  /** Flat [handle, model, paint, x, y, z, qx, qy, qz, qw, vx, vz] per drawn car (chassis centre). */
  publish(): number[] {
    const out: number[] = [];
    for (const car of this.active.values()) {
      const p = car.pose;
      if (!Number.isFinite(p.y)) continue;
      const q = car.rot;
      const { indicator, braking } = vehicleLights(car.motion, this.time);
      out.push(car.handle, car.model, car.paint, round(p.x), round(p.y + CHASSIS_LIFT), round(p.z),
        round4(q.x), round4(q.y), round4(q.z), round4(q.w), round(p.hx * p.speed), round(p.hz * p.speed),
        car.seed ??= hash32('appearance', car.key), indicator, (braking ? 1 : 0) | (honking(car.motion) ? 2 : 0), round(p.speed),
        round4(car.wheelY[0]), round4(car.wheelY[1]), round4(car.wheelY[2]), round4(car.wheelY[3]));
    }
    return out;
  }

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
    this.stats.hour = this.hourAt(t);
    let deferred = 0;
    for (const car of this.active.values()) {
      if (!this.network.isEnabled(car.lane.tile) || this.claims.has(car.key)) { this.deactivate(car); continue; }
      const d = Math.hypot(car.pose.x - view.x, car.pose.z - view.z);
      car.visible = inView(view, car.pose.x, car.pose.z);
      const motion = this.flow.cars.get(car.key);
      const present = !!motion && this.flow.desired(motion, t);
      // A car on an emptied slot goes where a new one could appear: out of view, or in view beyond the creation
      // minimum (so a busier chain's cars do not add to the next street's own beyond it).
      if (mustRemove(d, car.visible) || (!present && (!car.visible || this.fill || d > RING.createInViewMin))) this.deactivate(car);
      else if (!present) deferred++;
    }
    this.stats.deferred = deferred;
    if (this.active.size >= this.config.cap) { this.fill = false; return; }
    const candidates: { motion: FlowCar; lane: Lane; n: number; d: number; key: string }[] = [];
    for (const motion of this.flow.cars.values()) {
      const { key, lane, n } = motion;
      if (this.byKey.has(key) || this.claims.has(key) || !this.flow.desired(motion, t)) continue;
      const { x, z } = interpolateFlowPose(motion, (t - this.flow.tick * FLOW_STEP) / FLOW_STEP, () => 0);
      const d = Math.hypot(x - view.x, z - view.z);
      if (!mayCreate(d, inView(view, x, z), this.fill) || !this.host.resident(x, z)) continue;
      candidates.push({ motion, lane, n, d, key });
    }
    candidates.sort((a, b) => a.d - b.d || (a.key < b.key ? -1 : 1));
    for (const c of candidates) {
      if (this.active.size >= this.config.cap) break;
      if (this.byKey.has(c.key)) continue;
      const car: ActiveCar = { motion: c.motion, handle: this.nextHandle++, lane: c.lane, n: c.n, key: c.key,
        model: c.motion.model, paint: pick(hash01('paint', c.key), PAINT_WEIGHTS),
        pose: blankPose(), body: null, visible: false, tier: 1, blend: 0, farAt: -Infinity, farX: 0, farZ: 0,
        rot: { x: 0, y: 0, z: 0, w: 1 }, wheelY: [0, 0, 0, 0], distance: 0 };
      interpolateFlowPose(car.motion, (t - this.flow.tick * FLOW_STEP) / FLOW_STEP, this.height, car.pose);
      this.active.set(car.handle, car); this.byKey.set(car.key, car);
    }
    this.fill = false;
    this.assignTiers(view);
  }

  private ranked: ActiveCar[] = [];
  /** Real / dummy / far with hysteresis, at the ring's 5 Hz: distance from the player (else the view). */
  assignTiers(view: View) {
    const at = this.host.player() ?? view, ranked = this.ranked;
    ranked.length = 0;
    for (const car of this.active.values()) {
      const dx = car.pose.x - at.x, dz = car.pose.z - at.z;
      car.distance = Number.isFinite(dx + dz) ? Math.sqrt(dx * dx + dz * dz) : Infinity;
      ranked.push(car);
    }
    ranked.sort((a, b) => a.distance - b.distance || a.handle - b.handle);
    let real = 0, far = 0;
    for (const car of ranked) {
      const d = car.distance, wasReal = car.tier === 0, visible = inView(view, car.pose.x, car.pose.z);
      const moving = !(car.motion.stopped >= TIERS.stopped);
      if (moving && visible && (d < TIERS.realIn || wasReal && d < TIERS.realOut) && (real < TIERS.realCount || wasReal && real < TIERS.realKeep)) {
        car.tier = 0; real++;
      } else if (d > TIERS.farIn || car.tier === 2 && d > TIERS.farOut) { car.tier = 2; far++; }
      else car.tier = 1;
    }
    this.stats.tiers = [real, ranked.length - real - far, far];
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
      if (!tile || tile.lanes[car.lane.index] !== car.lane || !this.network.isEnabled(car.lane.tile) || this.claims.has(car.key)) this.deactivate(car);
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
      .setTranslation(car.pose.x, car.pose.y + CHASSIS_LIFT, car.pose.z).setRotation(car.rot));
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

  /** Lane surface: collision samples every 4 m along the lane, sampled once per lane and cached (on real t0_0_0 lanes
   *  within 3.5 cm of the collision ground, where the surveyed lane Y is off by up to 0.21 m, p95 8.7 cm); the data's
   *  Y until they are in; null without either. Dummy and far cars stand on it; real cars sample their wheels. */
  private height = (lane: Lane, s: number): number | null => {
    const entry = this.heights.get(lane);
    if ((!entry || this.time > entry.retry) && !this.queued.has(lane)) { this.queued.add(lane); this.sampleQueue.push(lane); }
    if (entry) {
      const samples = entry.ys;
      if (samples.length === 1) { if (Number.isFinite(samples[0])) return samples[0]; }
      else {
        const f = Math.max(0, Math.min(samples.length - 1, s / SAMPLE_STEP)), i = Math.min(samples.length - 2, Math.floor(f));
        const a = samples[i], b = samples[i + 1], y = a + (b - a) * (f - i);
        if (Number.isFinite(y)) return y;
        if (!lane.ys) return Number.isFinite(a) ? a : Number.isFinite(b) ? b : null;
      }
    }
    return lane.ys ? interpolate(lane.ys, lane.cum, s) : null;
  };

  /** Samples queued lanes' road surface until the time `until`, following a lane under bridges by searching
   *  near its previous sample. A lane's heights apply once it is complete. */
  private sampleSome(until: number) {
    for (;;) {
      if (!this.sampling) {
        const lane = this.sampleQueue.shift();
        if (!lane) return;
        const count = Math.max(2, Math.ceil(lane.length / SAMPLE_STEP) + 1);
        this.sampling = { lane, out: new Float32Array(count).fill(NaN), i: 0, previous: null };
      }
      const job = this.sampling;
      for (; job.i < job.out.length; job.i++) {
        if (performance.now() > until) return;
        const along = Math.min(job.lane.length, job.i * SAMPLE_STEP), p = pointAlong(job.lane, along);
        if (!this.host.resident(p.x, p.z)) continue;
        // Search near the previous sample, or near the surveyed lane height (under bridges a top-down ray lands on the deck).
        const near = job.previous ?? (job.lane.ys ? interpolate(job.lane.ys, job.lane.cum, along) : null);
        let y = near !== null ? this.host.ground(p.x, near + 3, p.z, 8) : null;
        y ??= this.host.ground(p.x, 400, p.z, 500);
        if (y !== null) { job.out[job.i] = y; job.previous = y; }
      }
      if (job.i < job.out.length) return;
      // A lane with unresident samples is sampled again later, once its collision arrives.
      this.heights.set(job.lane, { ys: job.out, retry: job.out.some(v => !Number.isFinite(v)) ? this.time + 2 : Infinity });
      this.queued.delete(job.lane);
      this.sampling = null;
    }
  }
}

/** Physics chassis rotation (+X forward): heading yaw about Y, then pitch about the chassis Z axis. */
export function chassisRotation(p: CarPose): Quat {
  return chassisRotationInto(p, { x: 0, y: 0, z: 0, w: 1 });
}
export function chassisRotationInto(p: CarPose, out: Quat): Quat {
  const yaw = Math.atan2(-p.hz, p.hx);
  const sy = Math.sin(yaw / 2), cy = Math.cos(yaw / 2), sp = Math.sin(p.pitch / 2), cp = Math.cos(p.pitch / 2);
  out.x = sy * sp; out.y = sy * cp; out.z = cy * sp; out.w = cy * cp;
  return out;
}
/** `a` moved towards `b` by `f` (normalised lerp along the shorter arc; the angles here are small). */
function blendRotation(a: Quat, b: Quat, f: number) {
  const sign = a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w < 0 ? -1 : 1;
  const x = a.x + (sign * b.x - a.x) * f, y = a.y + (sign * b.y - a.y) * f, z = a.z + (sign * b.z - a.z) * f, w = a.w + (sign * b.w - a.w) * f;
  const d = Math.sqrt(x * x + y * y + z * z + w * w) || 1;
  a.x = x / d; a.y = y / d; a.z = z / d; a.w = w / d;
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
