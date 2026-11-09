// Rapier world used by the physics worker (and directly by the Node unit tests).
// Everything is in engine world-local coordinates; each .col tile's float64 origin becomes its collider translation.
import type * as RAPIER_NS from '@dimforge/rapier3d';
import { COLLISION_TILE, collisionTileAt, collisionTileId, distToCollisionTile, parseCollisionId, type V3 } from '../core/origin';
import { BODY_MARGIN, bodyReach, collisionCovers, coverageReach, easedSpeed, easeInput, easeReachLimit, PlayerCoverageHold } from './collisionCoverage';
import { GROUP, materialClass, MATERIALS, MAT } from '../shared/col/materials';
import { readCol, readColSync, COL_FLAG_FIX_INTERNAL_EDGES, type ColTile } from './colTile';
import { CellPlanner, compactPiece, InstallQueue, planCell, type CellPlan, type InstallJob, type Piece } from './tileInstall';
import { TriLocator } from './triLocator';
import { EdgeWalls } from './edgeWalls';
import { CHARACTER, DEBRIS, DT, MAX_SUBSTEPS, REMOTE_VEHICLE, VEHICLES, type VehicleTuning } from './constants';
import { Vehicle } from '../vehicle/vehicle';
import { CurbQueries } from '../vehicle/curbQueries';
import { remoteColliderHeight } from '../net/height';
import { addCurbMesh, addCurbPrimitive } from '../vehicle/tileCurbs.ts';
import { TrafficSim } from './traffic';
export { CHARACTER, DT };
import type { DamageHit } from '../vehicle/damage';
import type { CharacterInput, CharacterState, DebrisSpec, DebrisState, PoleSpec, PoleState, Quat, RayFilter, RayHit, RemoteBody, TileStats, VehicleInput, VehicleState, VehicleSpec } from './protocol';
import { chassisForward, type TrafficPedestrian, type TrafficVehicle } from '../traffic/pedestrians';

type R = typeof RAPIER_NS;
/** Install priority of decode-only checks and unparseable ids (as if this many metres away). */
const CHECK_PRIORITY = 150;
/** Triangle visits per planning piece (~1 ms). */
const PLAN_WORK_PER_PIECE = 60_000;
const parseCellId = (id: string) => parseCollisionId(id);
/** Below this speed (m/s) a driven car's easing follows the drive it is asked for, not its velocity. */
const EASE_REST_SPEED = 0.3;
/** Below this speed (m/s) an eased car creeping at the gap is held by its handbrake instead of braked. */
const EASE_ROLL_SPEED = 1.5;
/** Missing collision this close in time (s) ahead of the driven car makes cell installs urgent. */
const COVERAGE_URGENT_S = 4;

export { COVERAGE_HOLD_SECONDS } from './collisionCoverage';

/** Rapier interaction groups: high 16 bits membership, low 16 bits filter. */
/** A remote proxy moving farther than this in one update is placed, not swept (m). */
const REMOTE_JUMP_M = 3;
/** A proxy is swept towards its newest pose at most at the owner's reported speed times REMOTE_SWEEP.scale plus
 *  REMOTE_SWEEP.slack (m/s), one fixed substep at a time. Setting the whole move as one substep's kinematic target made
 *  its velocity move / (1/60 s): a resting shared car whose pose caught up 1.1 m launched the local car it touched at
 *  66 m/s (verify-all r2 mac-turku: 1.6 -> 73.9 m/s in one step), and a remote car at 20 m/s seen at 20 fps pushed
 *  like a 60 m/s wall. */
const REMOTE_SWEEP = { scale: 1.25, slack: 1 } as const;
/** A gap the sweep would need longer than REMOTE_PLACE_S to close (at least REMOTE_PLACE_MIN_M) is placed instead, so a
 *  resting car's late pose or a reset leaves no invisible proxy behind its drawn car; a placed proxy overlapping a
 *  local car by up to 1.8 m pushes it out at under 3 m/s (Rapier's depenetration). */
const REMOTE_PLACE_S = 0.25, REMOTE_PLACE_MIN_M = 0.5;
/** A remote car's proxy is a dynamic body of its class mass without gravity, so a hit splits momentum by the two masses
 *  as with a parked car (a kinematic proxy was a wall of infinite mass: Jonni, city.lab 2026-11-09, "the bots feel rock
 *  solid"; a spring back to the owner's pose felt "way too springy": it must behave as any other NPC or parked car).
 *  - follow: velocity = the owner's velocity plus the offset to its newest pose (advanced along that velocity) over
 *    `tau`, at most the old sweep speed: first order, no overshoot.
 *  - free, for `freeS` after a contact with a local car or the character: no pull to the owner's (pre-hit) pose. The
 *    velocity the solver gave it beyond the owner's dies away at `slide` m/s² (a car shoved on its tyres), its spin at
 *    `spin` 1/s; height, pitch and roll follow the owner's. The drawn car follows the proxy (remoteProxies).
 *  - return: glides onto the owner's pose over `returnTau` (first order) at most `returnSlack` m/s faster than the
 *    owner, until within `doneM`; by then the owner's pose shows how its own client took the same hit. */
const REMOTE_SERVO = { tau: 0.08, freeS: 1, slide: 7.5, spin: 3, returnTau: 0.3, returnSlack: 4, doneM: 0.05, extrapolateS: 0.2,
  /** A hit (closing faster than this, m/s) frees the proxy; a resting touch does not, so a car its owner drives away
   *  from a local car leaning on it follows its owner. */
  impact: 1 } as const;
/** The server's contact impulse (PROTOCOL.md vehicle_impulse) waits IMPULSE_HOLD_S and is dropped when the car touched
 *  a remote proxy from IMPULSE_DEDUP_S before its arrival until then: this client already exchanged that momentum in
 *  its own solver. It still moves a car the remote proxy never reached here (latency: a car hit from behind). */
const IMPULSE_HOLD_S = 0.15, IMPULSE_DEDUP_S = 0.5;
/** Other players' cars near this client run here as real vehicles: wheels, tyres, gravity and the world, driven by their
 *  owner's replicated controls (net/motion.ts) and pulled towards the owner's state predicted to the present (at most
 *  predictS ahead, turning with its spin): velocity towards the goal's plus the gap over `tau`, blended in over `velTau`;
 *  yaw likewise over `spinTau`, pitch and roll left to the suspension unless 0.3 rad off. A goal farther than `snapM`
 *  (plus 0.15 s of its speed) or `snapRad` off is placed (net/remotes.ts hides the jump). The nearest `max` within `near`
 *  m of this player (kept until `far`) on resident collision run so; the others stay proxies (REMOTE_SERVO). Ambient
 *  traffic passes them, as it passes parked cars.
 *  A contact between two players' cars has one authority, the player with the lower id (net/remotes.ts `lead`), so both
 *  clients see one outcome (crash lab 2026-11-09: each client letting its own car win left the two truths 1 m apart and
 *  the pulls then pushed the cars apart or on). The leader's copy of the other car runs on its owner's controls alone
 *  for `leaseS` after it last touched the leader's car, and the leader publishes that state; the other owner's car is
 *  pulled to it (`follow`) while it keeps arriving (`followS`), and its copy of the leader's car keeps its full pull. */
const REMOTE_SIM = { near: 60, far: 75, max: 5, predictS: 0.5, snapM: 4, snapRad: 0.8, tau: 0.25, velTau: 0.1, spinTau: 0.2,
  leaseS: 1, followS: 0.3 } as const;
type QuatLike = { x: number; y: number; z: number; w: number };
const qMul = (a: QuatLike, b: QuatLike): Quat => ({ x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y, y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
  z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w, w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z });
/** The rotation `spin` (rad/s, world frame) turns through in `t` seconds. */
const qSpin = (spin: V3, t: number): Quat => {
  const rate = Math.hypot(spin.x, spin.y, spin.z), half = rate * t / 2, k = rate > 1e-9 ? Math.sin(half) / rate : 0;
  return { x: spin.x * k, y: spin.y * k, z: spin.z * k, w: Math.cos(half) };
};
/** `v` turned by `a` rad about +Y. */
const turnY = (v: V3, a: number): V3 => ({ x: v.x * Math.cos(a) + v.z * Math.sin(a), y: v.y, z: -v.x * Math.sin(a) + v.z * Math.cos(a) });
interface RemoteCar { vehicle: Vehicle; model: string; st: number; input: VehicleInput | null; snaps: number;
  /** This client leads contacts with the car, and until which tick it holds the car (REMOTE_SIM). */
  lead: boolean; leaseUntil: number;
  /** The owner's state and the local tick it was true at. */
  target: { pos: V3; rot: Quat; vel: V3; spin: V3; tick: number } }
const groups = (membership: number, filter: number) => ((membership & 0xffff) << 16) | (filter & 0xffff);

/** One solver step of `world`. Rapier 0.21's World.step() also wraps, after every step, what the engine created on
 *  its own (soft-body tear pieces): a JS-WASM call per collider and rigid body of the whole world. The game creates
 *  no soft bodies, and that walk was 30 % of the physics worker's step on the Windows laptop (44 s of 145 s in 5.5
 *  minutes of city.lab, frame-mp win-prof-1), six times the solver itself (stepWithEvents 7 s). */
export function stepPipeline(world: RAPIER_NS.World, events?: RAPIER_NS.EventQueue, hooks?: RAPIER_NS.PhysicsHooks) {
  world.physicsPipeline.step(world.gravity, world.integrationParameters, world.islands, world.broadPhase, world.narrowPhase,
    world.bodies, world.colliders, world.softBodies, world.impulseJoints, world.multibodyJoints, world.ccdSolver, events, hooks);
}

interface ColliderInfo { tile: string; triMat: Uint8Array | null; mat: number; locator: TriLocator | null }
interface LoadedTile { stats: TileStats; colliders: RAPIER_NS.Collider[]; cell: [number, number] }

export class PhysicsWorld {
  readonly world: RAPIER_NS.World;
  private tiles = new Map<string, LoadedTile>();
  private installs = new InstallQueue();
  private residentCells = new Set<string>();
  private heldVehicles = new Map<string, { velocity: V3; angular: V3 }>();
  private loadingCells = new Set<string>();
  private coming = new Set<string>();
  private coverageHold = new PlayerCoverageHold();
  get coverageBlock() { return this.coverageHold.state; }
  /** Speed (m/s) the driven car is eased to before collision that has not arrived (null while not eased). */
  private easing: number | null = null;
  /** Seconds until the driven car would reach collision that is not installed (Infinity when none within the search). */
  private coverageLead = Infinity;
  /** The driven car reaches missing collision within COVERAGE_URGENT_S, or is held: cell installs get the larger budget. */
  get coverageUrgent() { return this.coverageLead < COVERAGE_URGENT_S || this.coverageHold.state !== null; }

  private vehicleRadii = new Map<string, number>();
  private colliderInfo = new Map<number, ColliderInfo>();
  private acc = 0;
  tick = 0;
  previousCharacter: CharacterState | null = null;
  previousVehicle: VehicleState | null = null;
  private jumpPending = false;
  get remainder() { return this.acc; }

  // character
  private cc: RAPIER_NS.KinematicCharacterController | null = null;
  private charCollider: RAPIER_NS.Collider | null = null;
  private charVy = 0;
  private charGrounded = false;
  private charVel = { x: 0, y: 0, z: 0 };
  private charEnabled = true;

  // vehicle
  private vehicles = new Map<string, Vehicle>();
  private vehicleSnapshots = new Map<string, { state: VehicleState; sleeping: boolean }>();
  private poleSnapshots = new Map<string, { state: PoleState; resting: boolean }>();
  private drivenId: string | null = 'player';
  private nearbyVehicles = new Set<string>();
  private remotes = new Map<string, { body: RAPIER_NS.RigidBody; mode: RemoteBody['mode']; pos: V3; rot: Quat; vx: number; vz: number; at: number;
    /** Walkers: where the proxy is being swept to, and how fast it may get there (m/s; REMOTE_SWEEP). Cars: the
     *  servo's target pose (REMOTE_SERVO), set at `targetTick` and moving at `vel`. */
    target: V3 | null; maxSpeed: number; vel: V3; targetTick: number;
    /** Cars: REMOTE_SERVO phase; free until `freeUntil` (a tick); the velocity the servo set before this step's solve. */
    phase: 'follow' | 'free' | 'return'; freeUntil: number; solvedFrom?: V3 }>();
  /** Server contact impulses waiting for IMPULSE_HOLD_S, and the tick each local car last touched a remote car proxy. */
  /** Remote cars simulated here (REMOTE_SIM), and each remote's last model and driving tuning (null: the class default). */
  private remoteCars = new Map<string, RemoteCar>();
  private remoteTunings = new Map<string, { model: string; tuning: VehicleTuning | null }>();
  /** Own cars a leading player holds in a contact: its state of the car, the tick it was true at and when it came. */
  private follows = new Map<string, { st: number; seen: number; target: RemoteCar['target'] }>();
  /** Own cars just placed (resetVehicle): leading states are ignored until the tick, as they predate the placement. */
  private followBlocked = new Map<string, number>();
  private pendingImpulses: { id: string; impulse: V3; arrived: number }[] = [];
  private remoteTouch = new Map<string, number>();
  private poles = new Map<string, { spec: PoleSpec; body: RAPIER_NS.RigidBody; anchor: RAPIER_NS.RigidBody; joint: RAPIER_NS.ImpulseJoint | null; brokenAt: number | null; brokeNow: boolean }>();
  private brokenPoles = new Set<string>();
  private poleEvents: RAPIER_NS.EventQueue;
  /** Car part debris, oldest first: its body, the tick it goes, and the last published state while it rests. */
  private debris = new Map<string, { body: RAPIER_NS.RigidBody; until: number; solidAt: number; floor: number; state: DebrisState | null }>();
  get vehicle(): Vehicle | null { return this.vehicles.get('player') ?? null; }
  private curbQueries: CurbQueries;
  /** Walls along the true edges of the collision (edgeWalls.ts). */
  readonly edges: EdgeWalls;
  /** Ambient traffic, created by the first traffic message (physics/traffic.ts). */
  traffic: TrafficSim | null = null;
  /** Traffic time at the end of the next step() (s); substeps run at it minus the steps still to come. */
  trafficTime = 0;
  /** Rigid-body handles of cars claimed from traffic by this client. */
  private claimedBodies = new Map<number, string>();

  constructor(private readonly R: R) {
    this.world = new R.World({ x: 0, y: -9.81, z: 0 });
    this.world.timestep = DT;
    // Removing a rigid body unmaps its own colliders, then (Rapier 0.21 RigidBodySet.remove) asks the engine about every
    // collider of the world, for ones a soft-body cluster took with it: one JS-WASM call per collider per removal
    // (269 ms for one remote proxy on the Windows laptop, frame-mp win-prof-3; traffic bodies come and go at 70 m all
    // the time). The game has no soft bodies (stepPipeline), so no collider ever leaves without its own unmap.
    this.world.colliders.unmapRemovedColliders = () => {};
    this.poleEvents = new R.EventQueue(true);
    this.curbQueries = new CurbQueries(R, this.world);
    this.edges = new EdgeWalls(R, this.world);
  }

  /** The traffic simulation, created on first use. */
  trafficSim(): TrafficSim {
    this.traffic ??= new TrafficSim(this.R, this.world, {
      ground: (x, top, z, depth) => this.raycast({ x, y: top, z }, { x: 0, y: -1, z: 0 }, depth, 'solid')?.point.y ?? null,
      resident: (x, z) => { const c = collisionTileAt(x, z); return this.residentCells.has(collisionTileId(c.i, c.j)); },
      spawnVehicle: (pos, yaw, spec) => this.spawnVehicle(pos, yaw, spec),
      damageVehicle: (id, hit) => this.damageVehicle(id, hit),
      impact: handle => {
        for (const vehicle of this.vehicles.values()) if (vehicle.body.handle === handle)
          return { pos: { ...vehicle.body.translation() }, vel: { ...(vehicle.velocityBeforeSolve ?? vehicle.body.linvel()) }, mass: vehicle.body.mass() };
        return null;
      },
      bodyKind: handle => {
        if (!this.charEnabled && this.vehicles.get(this.drivenId ?? '')?.body.handle === handle) return 'driven';
        if (this.claimedBodies.has(handle)) return 'claimed';
        for (const vehicle of this.vehicles.values()) if (vehicle.body.handle === handle) return 'vehicle';
        for (const car of this.remoteCars.values()) if (car.vehicle.body.handle === handle) return 'vehicle';
        return null;
      },
      pushCharacter: (dx, dz) => {
        if (!this.charCollider) return;
        const t = this.charCollider.translation();
        this.charCollider.setTranslation({ x: t.x + dx, y: t.y, z: t.z + dz });
      },
      character: () => {
        if (!this.charCollider || !this.charEnabled) return null;
        const t = this.charCollider.translation();
        return { x: t.x, y: t.y - CHARACTER.halfHeight - CHARACTER.radius - CHARACTER.skin, z: t.z };
      },
      player: () => {
        if (this.charCollider && this.charEnabled) return this.charCollider.translation();
        const driven = this.vehicles.get(this.drivenId ?? '');
        return driven ? driven.body.translation() : null;
      },
      players: () => {
        const people: TrafficPedestrian[] = [], vehicles: TrafficVehicle[] = [];
        const add = (id: string, vehicle: Vehicle) => {
          const v = vehicle.body.linvel();
          vehicles.push(trafficVehicle(id, vehicle.body.translation(), vehicle.body.rotation(), v.x, v.z, vehicle.halfExt));
        };
        const driven = this.vehicles.get(this.drivenId ?? '');
        if (this.charCollider && this.charEnabled) {
          const t = this.charCollider.translation();
          people.push({ id: -1, x: t.x, y: t.y - CHARACTER.halfHeight - CHARACTER.radius - CHARACTER.skin, z: t.z, vx: this.charVel.x, vz: this.charVel.z });
        }
        // The driven car, or the one the player just left in the street.
        if (driven) add('local', driven);
        // Cars this client claimed from traffic (crashed or abandoned) near the player: drivers go round them.
        const at = this.charCollider && this.charEnabled ? this.charCollider.translation() : driven?.body.translation();
        if (at) for (const id of this.claimedBodies.values()) {
          const vehicle = this.vehicles.get(id);
          if (!vehicle || id === this.drivenId) continue;
          const p = vehicle.body.translation();
          if (Math.hypot(p.x - at.x, p.z - at.z) < 150) add(id, vehicle);
        }
        let n = 0;
        for (const [id, r] of this.remotes) {
          n++;
          if (r.mode === 'foot') people.push({ id: -1 - n, x: r.pos.x, y: r.pos.y, z: r.pos.z, vx: r.vx, vz: r.vz });
          else vehicles.push(trafficVehicle(id, r.pos, r.rot, r.vx, r.vz, VEHICLES[r.mode].halfExt));
        }
        return { people, vehicles };
      },
    });
    return this.traffic;
  }

  setTileLoading(id: string, loading: boolean) {
    if (loading) this.loadingCells.add(id); else this.loadingCells.delete(id);
  }

  /** Unpublished cells of tiles still being built or sent (world/collisionStreaming.ts): their edge wall stands, but the
   *  driven car is eased to a stop before them rather than meeting the wall at speed. */
  setComing(add: string[], remove: string[]) {
    for (const id of remove) this.coming.delete(id);
    for (const id of add) this.coming.add(id);
  }

  // ------------------------------------------------------------------ tiles
  /** Decodes and installs a cell at once (Node tests and tools). The worker queues cells instead (requestTile). */
  async loadTile(id: string, buffer: ArrayBuffer): Promise<TileStats> {
    const t0 = performance.now();
    const col = await readCol(buffer);
    const plan = planCell(col);
    const colliders: RAPIER_NS.Collider[] = [], remap = new Int32Array(col.positions.length / 3).fill(-1);
    try { for (const piece of plan.pieces) this.buildPiece(id, col, piece, colliders, remap); }
    catch (error) { this.dropColliders(colliders); throw error; }
    return this.commitTile(id, col, plan, colliders, buffer.byteLength, performance.now() - t0);
  }

  /** Queues a cell install (worker path): decode, then its pieces under the per-tick budget of pumpInstalls,
   *  nearest the player first. The cell joins the world (and coverage) only when every piece exists; until then
   *  its colliders are disabled and an older version of the cell stays in use. `done` gets the stats, or the
   *  error (also when unloadTile cancels it). A newer request for the same id replaces this one silently. */
  requestTile(id: string, buffer: ArrayBuffer, done: (result: TileStats | Error) => void) {
    const t0 = performance.now(), ij = parseCellId(id);
    let col: ColTile | null = null, planner: CellPlanner | null = null, plan: CellPlan | null = null, remap: Int32Array | null = null;
    let next = 0, work = 0;
    const built: RAPIER_NS.Collider[] = [];
    const job: InstallJob = { key: id, priority: () => this.installPriority(ij), run: () => false, cancel: () => {} };
    const step = (): boolean => {
      if (!col) { job.phase = 'decode'; col = readColSync(buffer); planner = new CellPlanner(col); return false; }
      if (!plan) {
        job.phase = 'plan';
        if (!planner!.advance(PLAN_WORK_PER_PIECE)) return false;
        plan = planner!.plan!; remap = new Int32Array(col.positions.length / 3).fill(-1);
        return false;
      }
      if (next < plan.pieces.length) {
        const from = built.length, piece = plan.pieces[next++];
        job.phase = piece.kind === 'mesh' ? `mesh:${piece.tris.length}` : `prims:${piece.to - piece.from}`;
        this.buildPiece(id, col, piece, built, remap!);
        for (let k = from; k < built.length; k++) built[k].setEnabled(false);
        if (next < plan.pieces.length) return false;
      }
      job.phase = 'commit';
      for (const c of built) c.setEnabled(true);
      done(this.commitTile(id, col, plan, built, buffer.byteLength, work, performance.now() - t0));
      return true;
    };
    Object.assign(job, {
      run: () => {
        const w0 = performance.now();
        try { const finished = step(); work += performance.now() - w0; return finished; }
        catch (error) {
          this.dropColliders(built);
          done(error instanceof Error ? error : new Error(String(error)));
          return true;
        }
      },
      cancel: () => { this.dropColliders(built); done(Object.assign(new Error(`${id}: unloaded before its collider was built`), { cancelled: true })); },
    });
    this.installs.add(job);
  }

  /** Queues a decode-only check (draft tiles are drawn only after their collision passes it). */
  requestCheck(key: string, buffer: ArrayBuffer, done: (result: { tris: number; prims: number } | Error) => void) {
    this.installs.add({
      key: `check:${key}`,
      priority: () => CHECK_PRIORITY,
      run: () => {
        try {
          const col = readColSync(buffer);
          if (!col.indices.length && !col.prims.length) throw new Error(`${col.id}: no collision geometry`);
          done({ tris: col.indices.length / 3, prims: col.prims.length });
        } catch (error) { done(error instanceof Error ? error : new Error(String(error))); }
        return true;
      },
      cancel: () => {},
    });
  }

  /** Runs queued installs for up to `budgetMs` (at least one piece). Returns the time used (ms). */
  pumpInstalls(budgetMs: number): number { return this.installs.pump(budgetMs); }
  /** Queued cell installs and checks, and the longest piece of the latest pump. */
  get installState() { return { queued: this.installs.size, slowest: this.installs.slowest }; }

  /** Cells under or nearest the player's actor (and any held vehicle) install first; ahead of a moving car a cell counts
   *  nearer by half its distance along the heading (world/collisionStreaming.ts requests cells in the same order). */
  private installPriority(ij: { i: number; j: number } | null): number {
    if (!ij) return CHECK_PRIORITY;
    const car = this.charCollider && this.charEnabled ? undefined : this.vehicles.get(this.drivenId ?? '');
    const at = this.charCollider && this.charEnabled ? this.charCollider.translation() : car?.body.translation();
    let d = at ? distToCollisionTile(at.x, at.z, ij.i, ij.j) : CHECK_PRIORITY;
    if (car && at) {
      const v = car.body.linvel(), speed = Math.hypot(v.x, v.z);
      if (speed > 3) d -= 0.5 * Math.max(0, (((ij.i + 0.5) * COLLISION_TILE - at.x) * v.x + ((ij.j + 0.5) * COLLISION_TILE - at.z) * v.z) / speed);
    }
    for (const id of this.heldVehicles.keys()) {
      const p = this.vehicles.get(id)?.body.translation();
      if (p) d = Math.min(d, distToCollisionTile(p.x, p.z, ij.i, ij.j));
    }
    return d;
  }

  /** Decodes a tile without adding it (draft tiles are drawn only after their collision passes this). */
  async checkTile(buffer: ArrayBuffer): Promise<{ tris: number; prims: number }> {
    const col = await readCol(buffer);
    if (!col.indices.length && !col.prims.length) throw new Error(`${col.id}: no collision geometry`);
    return { tris: col.indices.length / 3, prims: col.prims.length };
  }

  private commitTile(id: string, col: ColTile, plan: CellPlan, colliders: RAPIER_NS.Collider[], bytes: number, buildMs: number, waitMs?: number): TileStats {
    const stats: TileStats = { id, tris: plan.tris, prims: col.prims.length, bytes, buildMs: +buildMs.toFixed(2), colliders: colliders.length,
      curbTris: colliders.reduce((n, c) => n + this.curbQueries.triangleCount(c.handle), 0), pieces: plan.pieces.length,
      ...(waitMs !== undefined ? { waitMs: +waitMs.toFixed(1) } : {}) };
    // Build the replacement completely before retiring valid support.
    if (this.tiles.has(id)) this.unloadCommitted(id);
    this.tiles.set(id, { stats, colliders, cell: [col.tileI, col.tileJ] });
    this.residentCells.add(collisionTileId(col.tileI, col.tileJ));
    this.edges.add(col.tileI, col.tileJ, plan.lo, plan.hi);
    this.traffic?.collisionChanged(cellArea(col.tileI, col.tileJ));
    return stats;
  }

  private dropColliders(colliders: RAPIER_NS.Collider[]) {
    for (const c of colliders) { this.colliderInfo.delete(c.handle); this.curbQueries.remove(c.handle); this.world.removeCollider(c, false); }
    colliders.length = 0;
  }

  /** One install piece: a trimesh of one collision class (with its curb helper) or a batch of primitives. */
  private buildPiece(id: string, col: ColTile, piece: Piece, out: RAPIER_NS.Collider[], remap: Int32Array) {
    const R = this.R;
    const [ox, oy, oz] = col.origin;
    if (piece.kind === 'mesh') {
      const { cls, positions, indices, materials: triMat } = compactPiece(col, piece, remap);
      const flags = cls === 'solid' && (col.flags & COL_FLAG_FIX_INTERNAL_EDGES) !== 0 ? R.TriMeshFlags.FIX_INTERNAL_EDGES : undefined;
      const desc = R.ColliderDesc.trimesh(positions, indices, flags).setTranslation(ox, oy, oz);
      this.applyClass(desc, cls, 0.85);
      const c = this.world.createCollider(desc);
      out.push(c);
      this.colliderInfo.set(c.handle, { tile: id, triMat, mat: triMat[0], locator: new TriLocator(positions, indices, col.origin) });
      const helper = addCurbMesh(R, this.curbQueries, c, positions, indices, triMat, col.origin);
      if (helper) out.push(helper);
      return;
    }
    for (let k = piece.from; k < piece.to; k++) {
      const p = col.prims[k];
      let desc: RAPIER_NS.ColliderDesc | null = null;
      if (p.kind === 1) desc = R.ColliderDesc.cuboid(p.params[0], p.params[1], p.params[2]);
      else if (p.kind === 2) desc = R.ColliderDesc.capsule(p.params[0], p.params[1]);
      else if (p.kind === 3) desc = R.ColliderDesc.cylinder(p.params[0], p.params[1]);
      if (!desc) { console.warn(`[physics] ${id}: unknown primitive kind ${p.kind}, skipped`); continue; }
      desc.setTranslation(ox + p.center[0], oy + p.center[1], oz + p.center[2])
        .setRotation({ x: p.rotation[0], y: p.rotation[1], z: p.rotation[2], w: p.rotation[3] });
      this.applyClass(desc, materialClass(p.material), MATERIALS[p.material]?.friction ?? 0.8);
      const c = this.world.createCollider(desc);
      out.push(c);
      this.colliderInfo.set(c.handle, { tile: id, triMat: null, mat: p.material, locator: null });
      const helper = addCurbPrimitive(R, this.curbQueries, c, p.material);
      if (helper) out.push(helper);
    }
  }

  private applyClass(desc: RAPIER_NS.ColliderDesc, cls: 'solid' | 'query' | 'sensor', friction: number) {
    if (cls === 'solid') desc.setCollisionGroups(groups(GROUP.STATIC_SOLID, GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC | 0xff00)).setFriction(friction);
    else if (cls === 'query') desc.setCollisionGroups(groups(GROUP.STATIC_QUERY, 0xff00)).setSolverGroups(0);
    // 0xff00: scene queries; only the 'water' ray filter includes sensors, every other query excludes them.
    else desc.setSensor(true).setCollisionGroups(groups(GROUP.STATIC_SENSOR, GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC | 0xff00));
  }

  /** Removes a cell, and cancels its queued install (whose `done` then gets an error). */
  unloadTile(id: string): boolean {
    const cancelled = this.installs.cancel(id);
    return this.unloadCommitted(id) || cancelled;
  }

  private unloadCommitted(id: string): boolean {
    const t = this.tiles.get(id);
    if (!t) return false;
    this.dropColliders(t.colliders);
    this.tiles.delete(id);
    this.residentCells.delete(collisionTileId(...t.cell));
    this.edges.remove(...t.cell);
    this.traffic?.collisionChanged(cellArea(...t.cell));
    return true;
  }

  tileStats(): TileStats[] { return [...this.tiles.values()].map((t) => t.stats); }
  hasTile(id: string): boolean { return this.tiles.has(id); }

  // ------------------------------------------------------------------ character
  spawnCharacter(feet: V3) {
    const R = this.R;
    if (!this.cc) {
      const cc = this.world.createCharacterController(CHARACTER.skin);
      cc.setUp({ x: 0, y: 1, z: 0 });
      cc.enableAutostep(0.2, CHARACTER.autostepMinWidth, false);
      cc.enableSnapToGround(CHARACTER.snap);
      cc.setMaxSlopeClimbAngle((CHARACTER.maxClimbDeg * Math.PI) / 180);
      cc.setMinSlopeSlideAngle((CHARACTER.minSlideDeg * Math.PI) / 180);
      cc.setSlideEnabled(true);
      cc.setApplyImpulsesToDynamicBodies(true);
      this.cc = cc;
      this.charCollider = this.world.createCollider(
        R.ColliderDesc.capsule(CHARACTER.halfHeight, CHARACTER.radius)
          .setCollisionGroups(groups(GROUP.CHARACTER, GROUP.STATIC_SOLID | GROUP.VEHICLE | GROUP.DYNAMIC | REMOTE_VEHICLE)),
      );
    }
    this.coverageHold.clear('character');
    this.setCharacterFeet(feet);
    this.charVy = 0;
    this.charGrounded = false;
    this.charEnabled = true;
    this.charCollider!.setEnabled(true);
  }

  private setCharacterFeet(feet: V3) {
    this.charCollider!.setTranslation({ x: feet.x, y: feet.y + CHARACTER.halfHeight + CHARACTER.radius + CHARACTER.skin, z: feet.z });
  }

  setCharacterEnabled(enabled: boolean, feet?: V3) {
    if (!this.charCollider) return;
    if (!enabled || feet) this.coverageHold.clear('character');
    if (feet) this.setCharacterFeet(feet);
    this.charEnabled = enabled;
    this.charCollider.setEnabled(enabled);
    this.charVy = 0;
  }

  private stepCharacter(input: CharacterInput) {
    const cc = this.cc!, col = this.charCollider!;
    const filter = groups(GROUP.CHARACTER, GROUP.STATIC_SOLID | GROUP.VEHICLE | GROUP.DYNAMIC | REMOTE_VEHICLE);
    const flags = this.R.QueryFilterFlags.EXCLUDE_SENSORS;
    const p0 = col.translation();
    if (input.fly) {
      // Flight keeps real collisions, but crosses artificial playable-area barriers.
      cc.computeColliderMovement(col, { x: input.vx * DT, y: input.fly.vy * DT, z: input.vz * DT }, flags, filter,
        collider => !this.edges.isWall(collider));
      const m = cc.computedMovement();
      col.setTranslation({ x: p0.x + m.x, y: p0.y + m.y, z: p0.z + m.z });
      this.charVy = 0;
      this.charGrounded = cc.computedGrounded();
      this.charVel = { x: m.x / DT, y: m.y / DT, z: m.z / DT };
      return;
    }
    // Rapier's native autostep depends on horizontal speed. A second, height-limited
    // clearance move below handles slow walks into steps up to 0.35 m.
    if (this.charGrounded && input.jump) this.charVy = CHARACTER.jumpSpeed;
    else if (this.charGrounded && this.charVy <= 0) this.charVy = CHARACTER.groundedVy;
    else this.charVy = Math.max(this.charVy - CHARACTER.gravity * DT, -50);
    const wanted = this.charVy * DT;
    cc.computeColliderMovement(col, { x: input.vx * DT, y: wanted, z: input.vz * DT }, flags, filter);
    let m = cc.computedMovement();
    let grounded = cc.computedGrounded();
    if (this.charGrounded && !input.jump && Math.hypot(input.vx, input.vz) > 0.01) {
      const requested = Math.hypot(input.vx, input.vz) * DT;
      const progressed = (m.x * input.vx + m.z * input.vz) / Math.hypot(input.vx, input.vz);
      if (progressed < requested * 0.5) {
        const speed = Math.hypot(input.vx, input.vz);
        const ahead = (CHARACTER.radius + requested) / speed;
        const feetY = p0.y - CHARACTER.halfHeight - CHARACTER.radius - CHARACTER.skin;
        const rear = this.raycast(
          { x: p0.x - input.vx * CHARACTER.radius / speed, y: feetY + 0.2, z: p0.z - input.vz * CHARACTER.radius / speed },
          { x: 0, y: -1, z: 0 }, 0.7,
        );
        const baseY = Math.min(feetY, rear?.point.y ?? feetY);
        const support = this.raycast(
          { x: p0.x + input.vx * ahead, y: feetY + CHARACTER.autostepHeight + 0.1, z: p0.z + input.vz * ahead },
          { x: 0, y: -1, z: 0 }, CHARACTER.autostepHeight + 0.4,
        );
        if (support && support.point.y - baseY > CHARACTER.autostepHeight + 0.002) {
          // The surface ahead is above the climb limit, even if capsule curvature
          // would let repeated small moves inch up its vertical face.
          col.setTranslation({ x: p0.x + m.x, y: p0.y + m.y, z: p0.z + m.z });
          this.charGrounded = cc.computedGrounded();
          this.charVel = { x: m.x / DT, y: m.y / DT, z: m.z / DT };
          return;
        }
        const raised = CHARACTER.autostepHeight + CHARACTER.skin;
        cc.computeColliderMovement(col, { x: 0, y: raised, z: 0 }, flags, filter);
        const up = cc.computedMovement();
        if (up.y >= raised - CHARACTER.skin) {
          col.setTranslation({ x: p0.x, y: p0.y + up.y, z: p0.z });
          cc.computeColliderMovement(col, { x: input.vx * DT, y: 0, z: input.vz * DT }, flags, filter);
          const across = cc.computedMovement();
          const advance = (across.x * input.vx + across.z * input.vz) / Math.hypot(input.vx, input.vz);
          if (advance > progressed + 0.001) {
            col.setTranslation({ x: p0.x + across.x, y: p0.y + up.y + across.y, z: p0.z + across.z });
            cc.computeColliderMovement(col, { x: 0, y: -(raised + CHARACTER.snap), z: 0 }, flags, filter);
            const down = cc.computedMovement();
            const rise = up.y + across.y + down.y;
            if (cc.computedGrounded() && rise > -CHARACTER.skin && rise <= CHARACTER.autostepHeight + 0.002) {
              m = { x: across.x, y: rise, z: across.z };
              grounded = true;
            }
          }
          col.setTranslation(p0);
        }
      }
    }
    // Below the slide angle, Rapier's controller still leaks part of the grounded push down the slope: an idle
    // player crept 1-3 mm/s downhill with millimetre pops on 8-35 degree hills, and the camera crept with it. A
    // character that stays grounded with no input, moves less than a centimetre this step and stands on ground
    // flatter than the slide angle holds its place; steeper ground still slides.
    const idle = this.charGrounded && grounded && !input.jump && Math.hypot(input.vx, input.vz) <= 0.01;
    if (idle && Math.hypot(m.x, m.y, m.z) < 0.01) {
      const ground = this.raycast(p0, { x: 0, y: -1, z: 0 }, CHARACTER.halfHeight + CHARACTER.radius + 0.4, 'solid');
      if (ground && ground.normal.y >= Math.cos(CHARACTER.minSlideDeg * Math.PI / 180)) m = { x: 0, y: 0, z: 0 };
    }
    const p = { x: p0.x + m.x, y: p0.y + m.y, z: p0.z + m.z };
    col.setTranslation(p);
    this.charGrounded = grounded;
    if (this.charVy > 0 && m.y < wanted * 0.5) this.charVy = 0; // bumped head
    this.charVel = { x: (p.x - p0.x) / DT, y: (p.y - p0.y) / DT, z: (p.z - p0.z) / DT };
  }

  characterState(): CharacterState | null {
    if (!this.charCollider) return null;
    const t = this.charCollider.translation();
    const feet = { x: t.x, y: t.y - CHARACTER.halfHeight - CHARACTER.radius - CHARACTER.skin, z: t.z };
    let groundMaterial = 0;
    if (this.charEnabled) {
      const hit = this.raycast({ x: t.x, y: t.y, z: t.z }, { x: 0, y: -1, z: 0 }, CHARACTER.halfHeight + CHARACTER.radius + 0.4, 'solid');
      groundMaterial = hit?.material ?? 0;
    }
    return { pos: feet, vel: { ...this.charVel }, grounded: this.charGrounded, enabled: this.charEnabled, groundMaterial };
  }

  // ------------------------------------------------------------------ vehicle
  spawnVehicle(pos: V3, yaw: number, spec: Partial<VehicleSpec> = {}) {
    const id = spec.id ?? 'player';
    const existing = this.vehicles.get(id);
    if (existing) {
      this.resetVehicle(pos, yaw, id);
      if (spec.rot) existing.body.setRotation(spec.rot, true);
      if (spec.linvel) existing.body.setLinvel(spec.linvel, true);
      return;
    }
    this.vehicles.set(id, new Vehicle(this.R, this.world, pos, yaw, this.curbQueries, spec, (collider, point) => this.materialAt(collider, point)));
    const created = this.vehicles.get(id)!;
    if (spec.rot) created.body.setRotation(spec.rot, true);
    if (spec.linvel) created.body.setLinvel(spec.linvel, true);
    const h = spec.halfExt ?? spec.tuning?.halfExt ?? VEHICLES[spec.kind ?? 'car'].halfExt;
    this.vehicleRadii.set(id, Math.hypot(h.x, h.y, h.z) + 0.5);
    if (id.startsWith('tr:')) this.claimedBodies.set(created.body.handle, id);
  }

  removeVehicle(id: string) {
    if (id === this.drivenId) return;
    const vehicle = this.vehicles.get(id);
    if (!vehicle) return;
    this.claimedBodies.delete(vehicle.body.handle);
    this.world.removeVehicleController(vehicle.controller);
    this.world.removeRigidBody(vehicle.body);
    this.vehicles.delete(id);
    this.vehicleRadii.delete(id);
    this.vehicleSnapshots.delete(id);
    this.heldVehicles.delete(id);
  }

  driveVehicle(id: string | null) {
    if (id && !this.vehicles.has(id)) throw new Error(`unknown vehicle ${id}`);
    this.coverageHold.clear();
    const previous = this.vehicles.get(this.drivenId ?? "");
    if (previous) { previous.horn = false; previous.seated = false; }
    this.drivenId = id;
    if (id) { const vehicle = this.vehicles.get(id)!; vehicle.lights = 1; vehicle.seated = true; vehicle.body.wakeUp(); vehicle.ownDamage(); }
  }
  resetVehicle(pos: V3, yaw: number, id = 'player') {
    const car = this.vehicles.get(id);
    if (!car) return;
    this.coverageHold.clear(id);
    this.heldVehicles.delete(id);
    // A leading player's states of the car from before the placement no longer apply.
    this.follows.delete(id); this.followBlocked.set(id, this.tick + Math.round(REMOTE_SIM.followS / DT));
    car.body.setBodyType(this.R.RigidBodyType.Dynamic, true);
    car.reset(pos, yaw);
    this.vehicleSnapshots.delete(id);
  }
  /** The car `vehicleState` reports: the driven one, else the own start car. */
  get ownVehicleId() { return this.drivenId ?? 'player'; }
  vehicleState(): VehicleState | null {
    const id = this.ownVehicleId, vehicle = this.vehicles.get(id);
    return vehicle ? { id, ...vehicle.state() } : null;
  }
  vehicleStates(): VehicleState[] {
    return [...this.vehicles].map(([id, vehicle]) => {
      const sleeping = vehicle.body.isSleeping(), old = this.vehicleSnapshots.get(id);
      if (old && sleeping && old.sleeping && old.state.lights === vehicle.lights && old.state.horn === vehicle.horn
        && old.state.damage === vehicle.damageWire()) return old.state;
      // Asleep or held for missing collision (Fixed): flagged, since an asleep car's state is the one it fell asleep with.
      const state: VehicleState = { id, ...vehicle.state(), ...(sleeping ? { asleep: true } : {}), ...(this.heldVehicles.has(id) ? { held: true } : {}),
        ...(id === this.drivenId && this.easing !== null ? { eased: +this.easing.toFixed(1) } : {}) };
      this.vehicleSnapshots.set(id, { state, sleeping });
      return state;
    });
  }

  vehicleImpulse(id: string, impulse: V3) {
    if (!this.vehicles.has(id) || ![impulse.x, impulse.y, impulse.z].every(Number.isFinite)) return;
    this.pendingImpulses.push({ id, impulse, arrived: this.tick });
  }

  /** Applies the held server impulses whose hold is over, unless this client's solver already had that contact. */
  private applyImpulses() {
    if (!this.pendingImpulses.length) return;
    const hold = Math.round(IMPULSE_HOLD_S / DT), window = Math.round(IMPULSE_DEDUP_S / DT);
    this.pendingImpulses = this.pendingImpulses.filter(p => {
      if (this.tick - p.arrived < hold) return true;
      const touched = this.remoteTouch.get(p.id);
      if (touched === undefined || touched < p.arrived - window) this.applyVehicleImpulse(p.id, p.impulse);
      return false;
    });
  }

  private applyVehicleImpulse(id: string, impulse: V3) {
    const vehicle = this.vehicles.get(id);
    if (!vehicle) return;
    const size = Math.hypot(impulse.x, impulse.z);
    const scale = size ? Math.min(1, 12000 / size, vehicle.body.mass() * 8 / size) : 0;
    vehicle.body.applyImpulse({ x: impulse.x * scale, y: 0, z: impulse.z * scale }, true);
    vehicle.recordImpact(size * scale);
  }

  /** A hit on a damageable car's body (developer tools); a car without a damage cage ignores it. */
  damageVehicle(id: string, hit: DamageHit) {
    this.vehicles.get(id)?.damageHit(hit);
  }
  /** A scrape along a damageable car's face (developer tools). */
  scrapeVehicle(id: string, scrape: import('../vehicle/damage').DamageScrape) { this.vehicles.get(id)?.damageScrape(scrape); }
  /** A saved or shared damage state for a damageable car, or null for a fresh body. */
  setVehicleDamage(id: string, state: string | null) {
    this.vehicles.get(id)?.setDamage(state);
  }
  /** This client owns vehicle `id` (a shared record it holds): other players' cars damage it here too. */
  ownVehicleDamage(id: string) { this.vehicles.get(id)?.ownDamage(); }

  /** Remote proxies: a remote walker blocks walking (kinematic); a remote car (its own membership, REMOTE_VEHICLE) is a
   *  dynamic body of its class mass that the local chassis and character collide with (REMOTE_SERVO), so cars never
   *  pass through each other and a hit moves both by their masses. Wheel rays and ground queries do not see remote
   *  cars, and remote cars do not touch the ground, each other, traffic or debris. */
  setRemotes(bodies: RemoteBody[]) {
    const incoming = new Set(bodies.map(b => b.id));
    for (const [id, remote] of this.remotes) if (!incoming.has(id)) {
      this.world.removeRigidBody(remote.body);
      this.remotes.delete(id);
    }
    for (const id of [...this.remoteCars.keys()]) if (!incoming.has(id)) this.dropRemoteCar(id);
    for (const id of [...this.remoteTunings.keys()]) if (!incoming.has(id)) this.remoteTunings.delete(id);
    const simulated = this.simulateRemoteCars(bodies);
    for (const p of bodies) {
      if (simulated.has(p.id)) continue;
      let remote = this.remotes.get(p.id);
      if (remote?.mode !== p.mode) {
        if (remote) this.world.removeRigidBody(remote.body);
        const height = remoteColliderHeight(p.mode);
        const half = p.mode === 'foot' ? null : VEHICLES[p.mode].halfExt, mass = p.mode === 'foot' ? 0 : VEHICLES[p.mode].mass;
        const body = this.world.createRigidBody((half ? this.R.RigidBodyDesc.dynamic().setGravityScale(0).setCanSleep(false).setCcdEnabled(true)
          : this.R.RigidBodyDesc.kinematicPositionBased()).setTranslation(p.pos.x, p.pos.y + height, p.pos.z));
        if (half && p.rot) body.setRotation(p.rot, false);
        const desc = half ? this.R.ColliderDesc.cuboid(half.x, half.y, half.z).setMass(mass).setRestitution(0.05)
          : this.R.ColliderDesc.capsule(CHARACTER.halfHeight, CHARACTER.radius);
        desc.setCollisionGroups(half ? groups(REMOTE_VEHICLE, GROUP.CHARACTER | GROUP.VEHICLE) : groups(GROUP.DYNAMIC, GROUP.CHARACTER));
        this.world.createCollider(desc, body);
        remote = { body, mode: p.mode, pos: { ...p.pos }, rot: { x: 0, y: 0, z: 0, w: 1 }, vx: 0, vz: 0, at: performance.now(), target: null,
          maxSpeed: 0, vel: { x: 0, y: 0, z: 0 }, targetTick: this.tick, phase: 'follow', freeUntil: 0 };
        this.remotes.set(p.id, remote);
      }
      // Velocity from consecutive updates: ambient drivers predict where a remote player is going.
      const now = performance.now(), dt = (now - remote.at) / 1000;
      if (dt > 0.02) {
        const k = Math.min(1, dt / 0.25);
        remote.vx += ((p.pos.x - remote.pos.x) / dt - remote.vx) * k; remote.vz += ((p.pos.z - remote.pos.z) / dt - remote.vz) * k;
        if (Math.hypot(p.pos.x - remote.pos.x, p.pos.z - remote.pos.z) > 30) remote.vx = remote.vz = 0;
        remote.pos = { ...p.pos }; remote.at = now;
      }
      const y = p.pos.y + remoteColliderHeight(p.mode);
      const yaw = p.mode === 'foot' ? p.yaw : p.yaw + Math.PI / 2;
      remote.rot = p.mode !== 'foot' && p.rot ? p.rot : { x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) };
      // A jump (teleport, respawn, a late snapshot) is placed, never swept: a kinematic sweep through a local car would
      // launch it. Ordinary motion sweeps, so a remote car pushes and blocks like a moving wall.
      const at = remote.body.translation();
      if (remote.mode !== 'foot') {
        // A car is servoed in fixedStep (driveRemotes); only a jump (teleport, respawn, a late snapshot) is placed.
        // Without a reported velocity (tools, legacy peers): the pose's own motion per simulation tick, at any speed.
        const reported = !!p.vel && [p.vel.x, p.vel.y, p.vel.z].every(Number.isFinite);
        const prev = remote.target, ticks = this.tick - remote.targetTick;
        const vel = reported ? p.vel! : prev && ticks > 0 ? { x: (p.pos.x - prev.x) / (ticks * DT), y: (y - prev.y) / (ticks * DT),
          z: (p.pos.z - prev.z) / (ticks * DT) } : remote.vel;
        if (!reported && prev && ticks === 0) continue; // a second pose within one tick: no new information for the servo
        const jumped = !!prev && Math.hypot(p.pos.x - prev.x, y - prev.y, p.pos.z - prev.z) > REMOTE_JUMP_M + Math.hypot(vel.x, vel.z) * ticks * DT;
        remote.target = { x: p.pos.x, y, z: p.pos.z }; remote.vel = { ...vel }; remote.targetTick = this.tick;
        // As fast as a walker's sweep (REMOTE_SWEEP), so a late pose never flings the proxy into a car; a gap it could not
        // close within REMOTE_PLACE_S is placed. After a hit only the owner's own jump (a teleport, a respawn) is: a car
        // this client shoved stays where it slid, however far, and glides back (REMOTE_SERVO return).
        remote.maxSpeed = reported ? Math.hypot(vel.x, vel.y, vel.z) * REMOTE_SWEEP.scale + REMOTE_SWEEP.slack : Infinity;
        const placeBeyond = Math.min(REMOTE_JUMP_M, Math.max(REMOTE_PLACE_MIN_M, remote.maxSpeed * REMOTE_PLACE_S));
        if (remote.phase === 'follow' ? Math.hypot(p.pos.x - at.x, y - at.y, p.pos.z - at.z) > placeBeyond : jumped) {
          remote.body.setTranslation(remote.target, true); remote.body.setRotation(remote.rot, true);
          remote.body.setLinvel(vel, true); remote.body.setAngvel({ x: 0, y: 0, z: 0 }, true);
          remote.phase = 'follow';
        }
        continue;
      }
      const maxSpeed = p.vel ? Math.hypot(p.vel.x, p.vel.y, p.vel.z) * REMOTE_SWEEP.scale + REMOTE_SWEEP.slack : Infinity;
      const placeBeyond = Math.min(REMOTE_JUMP_M, Math.max(REMOTE_PLACE_MIN_M, maxSpeed * REMOTE_PLACE_S));
      if (Math.hypot(p.pos.x - at.x, y - at.y, p.pos.z - at.z) > placeBeyond) {
        remote.body.setTranslation({ x: p.pos.x, y, z: p.pos.z }, true); remote.body.setRotation(remote.rot, true);
        remote.target = null;
      } else {
        // Swept substep by substep in fixedStep (sweepRemotes). Without a reported velocity: the whole move at once.
        remote.target = { x: p.pos.x, y, z: p.pos.z };
        remote.maxSpeed = maxSpeed;
        remote.body.setNextKinematicRotation(remote.rot);
      }
    }
  }

  /** Picks the remote cars simulated here (REMOTE_SIM), creates or updates them and drops the rest; their proxies go. */
  private simulateRemoteCars(bodies: RemoteBody[]): Set<string> {
    const S = REMOTE_SIM, ref = this.remoteReference(), picked = new Set<string>(), candidates: { p: RemoteBody; d: number }[] = [];
    for (const p of bodies) if (p.model && p.tuning !== undefined) this.remoteTunings.set(p.id, { model: p.model, tuning: p.tuning });
    for (const p of bodies) {
      const known = this.remoteTunings.get(p.id);
      if (p.mode !== 'car' || !p.state || !ref || known?.model !== p.model) continue;
      const at = p.state.pos, d = Math.hypot(at.x - ref.x, at.z - ref.z), cell = collisionTileAt(at.x, at.z);
      if (d < (this.remoteCars.has(p.id) ? S.far : S.near) && this.residentCells.has(collisionTileId(cell.i, cell.j))) candidates.push({ p, d });
    }
    candidates.sort((a, b) => a.d - b.d);
    for (const { p } of candidates.slice(0, S.max)) picked.add(p.id);
    for (const id of [...this.remoteCars.keys()]) if (!picked.has(id)) this.dropRemoteCar(id);
    for (const p of bodies) if (picked.has(p.id)) this.updateRemoteCar(p, p.state!, this.remoteTunings.get(p.id)!);
    return picked;
  }

  /** Where this player is: the driven car, else the character. */
  private remoteReference(): V3 | null {
    const car = this.vehicles.get(this.drivenId ?? '');
    if (car && !this.charEnabled) return car.body.translation();
    return this.charCollider?.translation() ?? car?.body.translation() ?? null;
  }

  private updateRemoteCar(p: RemoteBody, s: import('./protocol').RemoteCarState, known: { model: string; tuning: VehicleTuning | null }) {
    const proxy = this.remotes.get(p.id);
    if (proxy) { this.world.removeRigidBody(proxy.body); this.remotes.delete(p.id); }
    let car = this.remoteCars.get(p.id);
    if (car && car.model !== known.model) { this.dropRemoteCar(p.id); car = undefined; }
    const target = { pos: { ...s.pos }, rot: { ...s.rot }, vel: { ...s.vel }, spin: { ...s.spin }, tick: this.tick - Math.round(s.age / DT) };
    if (!car) {
      const start = this.predictRemote(target);
      const vehicle = new Vehicle(this.R, this.world, start.pos, 0, this.curbQueries, { kind: 'car', ...(known.tuning ? { tuning: known.tuning } : {}) },
        (collider, point) => this.materialAt(collider, point));
      vehicle.body.setRotation(start.rot, true); vehicle.body.setLinvel(start.vel, true); vehicle.body.setAngvel(target.spin, true);
      this.remoteCars.set(p.id, { vehicle, model: known.model, st: s.st, input: s.input, snaps: 0, target, lead: !!p.lead, leaseUntil: -Infinity });
      return;
    }
    car.lead = !!p.lead;
    // The same owner state seen again keeps the tick it was first seen at; an older one (reordered) is ignored.
    if (s.st <= car.st) return;
    car.st = s.st; car.target = target; car.input = s.input;
  }

  private dropRemoteCar(id: string) {
    const car = this.remoteCars.get(id);
    if (!car) return;
    this.world.removeVehicleController(car.vehicle.controller);
    this.world.removeRigidBody(car.vehicle.body);
    this.remoteCars.delete(id);
  }

  /** The owner's state carried on to this tick: along its velocity turning with its yaw rate, rotated by its spin. */
  private predictRemote(t: RemoteCar['target']): { pos: V3; rot: Quat; vel: V3 } {
    const age = Math.max(0, Math.min(REMOTE_SIM.predictS, (this.tick - t.tick) * DT)), turn = t.spin.y * age, mid = turnY(t.vel, turn / 2);
    return { pos: { x: t.pos.x + mid.x * age, y: t.pos.y + t.vel.y * age, z: t.pos.z + mid.z * age }, rot: qMul(qSpin(t.spin, age), t.rot),
      vel: turnY(t.vel, turn) };
  }

  /** Steps each simulated remote car on its owner's controls and pulls it towards the predicted owner state, unless this
   *  client holds it in a contact; then pulls each own car a leading player holds towards that player's state of it. */
  private driveRemoteCars() {
    for (const car of this.remoteCars.values()) {
      car.vehicle.step(DT, car.input);
      const goal = this.predictRemote(car.target);
      // A hold ends early when the owner's own state is a placement away (the owner placed or reset its car).
      if (this.tick < car.leaseUntil && !this.placementAway(car.vehicle.body, goal)) continue;
      car.leaseUntil = -Infinity;
      if (this.pullTo(car.vehicle.body, goal, car.target.spin)) car.snaps++;
    }
    for (const [id, f] of this.follows) {
      const own = this.vehicles.get(id), goal = this.predictRemote(f.target);
      // A leading state a placement away is not this car's contact (stale, or from before a placement): ignored.
      if (!own || this.tick - f.seen > REMOTE_SIM.followS / DT || this.placementAway(own.body, goal)) { this.follows.delete(id); continue; }
      this.pullTo(own.body, goal, f.target.spin);
    }
  }

  /** `goal` is too far from `b` to pull towards: it is placed there (REMOTE_SIM snapM, snapRad). */
  private placementAway(b: RAPIER_NS.RigidBody, goal: { pos: V3; rot: Quat; vel: V3 }): boolean {
    const at = b.translation(), q = b.rotation(), err = qMul(goal.rot, { x: -q.x, y: -q.y, z: -q.z, w: q.w });
    const angle = 2 * Math.atan2(Math.hypot(err.x, err.y, err.z), Math.abs(err.w));
    return Math.hypot(goal.pos.x - at.x, goal.pos.y - at.y, goal.pos.z - at.z) > REMOTE_SIM.snapM + Math.hypot(goal.vel.x, goal.vel.z) * 0.15
      || angle > REMOTE_SIM.snapRad;
  }

  /** Pulls `b` towards `goal` (REMOTE_SIM), or places it there when it is too far off; true when placed. */
  private pullTo(b: RAPIER_NS.RigidBody, goal: { pos: V3; rot: Quat; vel: V3 }, spin: V3): boolean {
    const S = REMOTE_SIM, at = b.translation(), q = b.rotation();
    const ex = goal.pos.x - at.x, ey = goal.pos.y - at.y, ez = goal.pos.z - at.z;
    const err = qMul(goal.rot, { x: -q.x, y: -q.y, z: -q.z, w: q.w });
    if (err.w < 0) { err.x = -err.x; err.y = -err.y; err.z = -err.z; err.w = -err.w; }
    const sin = Math.hypot(err.x, err.y, err.z), angle = 2 * Math.atan2(sin, err.w), rate = sin > 1e-6 ? angle / sin : 0;
    if (this.placementAway(b, goal)) {
      b.setTranslation(goal.pos, true); b.setRotation(goal.rot, true); b.setLinvel(goal.vel, true); b.setAngvel(spin, true);
      return true;
    }
    const k = Math.min(1, DT / S.velTau), lv = b.linvel(), w = b.angvel();
    b.setLinvel({ x: lv.x + (goal.vel.x + ex / S.tau - lv.x) * k, y: lv.y + (goal.vel.y + ey / S.tau - lv.y) * k * 0.3,
      z: lv.z + (goal.vel.z + ez / S.tau - lv.z) * k }, true);
    const tilt = Math.hypot(err.x, err.z) * rate, kt = tilt > 0.3 ? k : 0;
    b.setAngvel({ x: w.x + (spin.x + err.x * rate / S.spinTau - w.x) * kt, y: w.y + (spin.y + err.y * rate / S.spinTau - w.y) * k,
      z: w.z + (spin.z + err.z * rate / S.spinTau - w.z) * kt }, true);
    return false;
  }

  /** A leading player's state of own car `id` (null: none): the car follows it while it keeps coming (REMOTE_SIM). */
  follow(id: string, s: import('./protocol').RemoteCarState | null) {
    if (!s) { this.follows.delete(id); return; }
    if (this.tick < (this.followBlocked.get(id) ?? -Infinity)) return;
    const known = this.follows.get(id);
    if (known && s.st <= known.st) return;
    this.follows.set(id, { st: s.st, seen: this.tick, target: { pos: { ...s.pos }, rot: { ...s.rot }, vel: { ...s.vel }, spin: { ...s.spin },
      tick: this.tick - Math.round(s.age / DT) } });
  }

  /** After the solve: a simulated remote car touching a local car or the character. A local car touching one remembers
   *  the tick (applyImpulses drops the server's impulse for a contact this solver had); the own driven car touching one
   *  this client leads starts or extends its hold (REMOTE_SIM leaseS). */
  private noteRemoteCarContacts(carIds: Map<number, string>) {
    for (const car of this.remoteCars.values()) {
      const b = car.vehicle.body;
      for (let i = 0; i < b.numColliders(); i++) {
        const own = b.collider(i);
        this.world.contactPairsWith(own, other => {
          const body = other.parent(), id = body ? carIds.get(body.handle) : undefined;
          if (id === undefined) return;
          let touching = false;
          this.world.contactPair(own, other, m => { if (m.numContacts() > 0) touching = true; });
          if (!touching) return;
          this.remoteTouch.set(id, this.tick);
          if (car.lead && id === this.drivenId) car.leaseUntil = this.tick + Math.round(REMOTE_SIM.leaseS / DT);
        });
      }
    }
  }

  /** The simulated remote cars as drawn (net/remotes.ts). */
  remoteCarDraws(): import('./protocol').RemoteCarDraw[] {
    return [...this.remoteCars].map(([id, car]) => {
      const b = car.vehicle.body, p = b.translation(), q = b.rotation(), held = this.tick < car.leaseUntil;
      return { id, pos: { x: p.x, y: p.y, z: p.z }, rot: { x: q.x, y: q.y, z: q.z, w: q.w }, vel: { ...b.linvel() }, snaps: car.snaps,
        ...(held ? { held: { vel: { ...b.linvel() }, spin: { ...b.angvel() } } } : {}) };
    });
  }

  /** This substep's kinematic target of each remote walker still on its way (REMOTE_SWEEP), and each remote car's
   *  servo velocity towards its owner's pose (REMOTE_SERVO). */
  private sweepRemotes() {
    for (const remote of this.remotes.values()) {
      const target = remote.target;
      if (!target) continue;
      if (remote.mode !== 'foot') { this.servoRemote(remote); continue; }
      const at = remote.body.translation(), dx = target.x - at.x, dy = target.y - at.y, dz = target.z - at.z;
      const d = Math.hypot(dx, dy, dz), max = remote.maxSpeed * DT;
      if (d <= max) { remote.body.setNextKinematicTranslation(target); remote.target = null; continue; }
      const k = max / d;
      remote.body.setNextKinematicTranslation({ x: at.x + dx * k, y: at.y + dy * k, z: at.z + dz * k });
    }
  }

  private servoRemote(remote: { body: RAPIER_NS.RigidBody; rot: Quat; target: V3 | null; vel: V3; targetTick: number; maxSpeed: number;
    phase: 'follow' | 'free' | 'return'; freeUntil: number; solvedFrom?: V3 }) {
    this.servoVelocity(remote);
    const v = remote.body.linvel();
    remote.solvedFrom = { x: v.x, y: v.y, z: v.z };
  }

  private servoVelocity(remote: { body: RAPIER_NS.RigidBody; rot: Quat; target: V3 | null; vel: V3; targetTick: number; maxSpeed: number;
    phase: 'follow' | 'free' | 'return'; freeUntil: number }) {
    const S = REMOTE_SERVO, b = remote.body, t = remote.target!, v = remote.vel, at = b.translation();
    if (remote.phase === 'free' && this.tick >= remote.freeUntil) remote.phase = 'return';
    const age = Math.min(S.extrapolateS, (this.tick - remote.targetTick) * DT);
    const goal = { x: t.x + v.x * age, y: t.y + v.y * age, z: t.z + v.z * age };
    // Rotation error q_target * conj(q) as an axis-angle rate over tau, the shorter way round.
    const tau = remote.phase === 'return' ? S.returnTau : S.tau;
    const q = b.rotation(), r = remote.rot;
    let ex = r.w * -q.x + r.x * q.w + r.y * -q.z - r.z * -q.y, ey = r.w * -q.y - r.x * -q.z + r.y * q.w + r.z * -q.x;
    let ez = r.w * -q.z + r.x * -q.y - r.y * -q.x + r.z * q.w, ew = r.w * q.w - r.x * -q.x - r.y * -q.y - r.z * -q.z;
    if (ew < 0) { ex = -ex; ey = -ey; ez = -ez; ew = -ew; }
    const sin = Math.hypot(ex, ey, ez), angle = 2 * Math.atan2(sin, ew), rate = sin > 1e-6 ? angle / sin / S.tau : 0;
    if (remote.phase === 'free') {
      // Shoved: the velocity beyond the owner's slides out on the tyres; no pull towards the owner's pre-hit pose.
      const lv = b.linvel(), rx = lv.x - v.x, rz = lv.z - v.z, rel = Math.hypot(rx, rz);
      const keep = rel > 0 ? Math.max(0, rel - S.slide * DT) / rel : 0;
      b.setLinvel({ x: v.x + rx * keep, y: v.y + (goal.y - at.y) / S.tau, z: v.z + rz * keep }, true);
      const w = b.angvel();
      b.setAngvel({ x: ex * rate, y: w.y * Math.max(0, 1 - S.spin * DT), z: ez * rate }, true);
      return;
    }
    const off = Math.hypot(goal.x - at.x, goal.y - at.y, goal.z - at.z);
    if (remote.phase === 'return' && off < S.doneM && angle < 0.01) remote.phase = 'follow';
    const want = { x: v.x + (goal.x - at.x) / tau, y: v.y + (goal.y - at.y) / tau, z: v.z + (goal.z - at.z) / tau };
    const limit = remote.phase === 'return' ? Math.hypot(v.x, v.y, v.z) + S.returnSlack : remote.maxSpeed;
    const fast = Math.hypot(want.x, want.y, want.z), cap = fast > limit ? limit / fast : 1;
    b.setLinvel({ x: want.x * cap, y: want.y * cap, z: want.z * cap }, true);
    const turn = rate * S.tau / tau;
    b.setAngvel({ x: ex * turn, y: ey * turn, z: ez * turn }, true);
  }

  /** Remote cars drawn at their proxy rather than their owner's pose: shoved by a local hit, or gliding back after one. */
  remoteProxies(): { id: string; pos: V3; rot: Quat }[] {
    const out: { id: string; pos: V3; rot: Quat }[] = [];
    for (const [id, r] of this.remotes) {
      if (r.mode === 'foot' || r.phase === 'follow') continue;
      const p = r.body.translation(), q = r.body.rotation();
      out.push({ id, pos: { x: p.x, y: p.y - remoteColliderHeight(r.mode), z: p.z }, rot: { x: q.x, y: q.y, z: q.z, w: q.w } });
    }
    return out;
  }

  /** After the solve: a remote car proxy hit by a local car or the character (REMOTE_SERVO.impact) goes free, and a
   *  local car touching one remembers the tick (applyImpulses drops the server's impulse for a contact this solver had). */
  private noteRemoteContacts(carIds: Map<number, string>) {
    for (const remote of this.remotes.values()) {
      if (remote.mode === 'foot') continue;
      const own = remote.body.collider(0);
      this.world.contactPairsWith(own, other => {
        let touching = false;
        this.world.contactPair(own, other, m => { if (m.numContacts() > 0) touching = true; });
        if (!touching) return;
        const body = other.parent(), id = body ? carIds.get(body.handle) : undefined;
        if (id === undefined && other !== this.charCollider) return;
        if (id !== undefined) this.remoteTouch.set(id, this.tick);
        // The velocity change this step's solve gave the proxy: a hit moved it, a resting touch barely does.
        const lv = remote.body.linvel(), before = remote.solvedFrom;
        if (!before || Math.hypot(lv.x - before.x, lv.z - before.z) < REMOTE_SERVO.impact) return;
        remote.phase = 'free'; remote.freeUntil = this.tick + Math.round(REMOTE_SERVO.freeS / DT);
      });
    }
  }

  setPoles(add: PoleSpec[], remove: string[]) {
    for (const id of remove) {
      this.poleSnapshots.delete(id);
      const pole = this.poles.get(id);
      if (!pole) continue;
      this.world.removeRigidBody(pole.body);
      this.world.removeRigidBody(pole.anchor);
      this.poles.delete(id);
    }
    for (const spec of add) {
      if (this.poles.has(spec.id)) continue;
      const centre = { x: spec.base.x, y: spec.base.y + spec.height / 2, z: spec.base.z };
      const anchor = this.world.createRigidBody(this.R.RigidBodyDesc.fixed().setTranslation(spec.base.x, spec.base.y, spec.base.z));
      const body = this.world.createRigidBody(this.R.RigidBodyDesc.dynamic().setTranslation(centre.x, centre.y, centre.z)
        .setCanSleep(true).setCcdEnabled(true).setLinearDamping(0.8).setAngularDamping(0.8));
      body.lockTranslations(true, false);
      body.lockRotations(true, false);
      this.world.createCollider(this.R.ColliderDesc.cylinder(spec.height / 2, 0.05)
        .setMass(12).setFriction(0.7).setActiveEvents(this.R.ActiveEvents.CONTACT_FORCE_EVENTS)
        .setCollisionGroups(groups(GROUP.DYNAMIC, GROUP.STATIC_SOLID | GROUP.CHARACTER | GROUP.VEHICLE | GROUP.DYNAMIC)), body);
      const joint = this.R.JointData.fixed({ x: 0, y: 0, z: 0 }, { x: 0, y: 0, z: 0, w: 1 },
        { x: 0, y: -spec.height / 2, z: 0 }, { x: 0, y: 0, z: 0, w: 1 });
      const attached = this.world.createImpulseJoint(joint, anchor, body, true);
      this.poles.set(spec.id, { spec, body, anchor, joint: attached, brokenAt: null, brokeNow: false });
      body.sleep();
      if (this.brokenPoles.has(spec.id)) {
        this.breakPole(spec.id, false);
        body.setRotation({ x: 0, y: 0, z: Math.SQRT1_2, w: Math.SQRT1_2 }, false);
        body.setTranslation({ x: spec.base.x + spec.height / 2, y: spec.base.y + 0.05, z: spec.base.z }, false);
        body.setBodyType(this.R.RigidBodyType.Fixed, true);
      }
    }
  }

  breakPole(id: string, local = false) {
    this.brokenPoles.add(id);
    const pole = this.poles.get(id);
    if (!pole || pole.brokenAt !== null) return;
    if (pole.joint) this.world.removeImpulseJoint(pole.joint, true);
    pole.joint = null;
    pole.body.lockTranslations(false, true);
    pole.body.lockRotations(false, true);
    pole.brokenAt = this.tick;
    pole.brokeNow = local;
    pole.body.wakeUp();
    pole.body.applyImpulse({ x: 0.7, y: 0.1, z: 0.7 }, true);
    pole.body.applyTorqueImpulse({ x: 0, y: 0, z: 8 }, true);
  }

  /** A car part that came off: a dynamic box at its drawn pose and velocity for DEBRIS.lifetimeS; the oldest piece
   *  goes when the cap is reached. Spawned on every client from the shared damage state, so each simulates its own. */
  spawnDebris(spec: DebrisSpec) {
    this.removeDebris(spec.id);
    while (this.debris.size >= DEBRIS.cap) this.removeDebris(this.debris.keys().next().value!);
    // A part is a shell, not a solid block: its box is a little smaller than its bounds. It must not start inside the
    // world (a kerb or a slope under a crushed nose, a wall it was pressed into): the solver would push it through the
    // thin ground. Lifted in 10 cm steps until it is clear (up to 1.6 m).
    const h = { x: Math.max(.02, spec.half.x * .85), y: Math.max(.02, spec.half.y * .85), z: Math.max(.02, spec.half.z * .85) };
    const shape = new this.R.Cuboid(h.x, h.y, h.z), pos = { ...spec.pos };
    for (let k = 0; k < 16; k++) {
      let blocked = false;
      this.world.intersectionsWithShape(pos, spec.rot, shape, () => { blocked = true; return false; },
        this.R.QueryFilterFlags.EXCLUDE_SENSORS, groups(0xff00, GROUP.STATIC_SOLID));
      if (!blocked) break;
      pos.y += .1;
    }
    const body = this.world.createRigidBody(this.R.RigidBodyDesc.dynamic().setTranslation(pos.x, pos.y, pos.z)
      .setRotation(spec.rot).setLinvel(spec.linvel.x, spec.linvel.y, spec.linvel.z).setAngvel(spec.angvel)
      .setCanSleep(true).setCcdEnabled(true).setLinearDamping(.3).setAngularDamping(.6));
    this.world.createCollider(this.R.ColliderDesc.cuboid(h.x, h.y, h.z).setMass(spec.mass)
      .setFriction(.6).setRestitution(.15)
      .setCollisionGroups(groups(DEBRIS.group, GROUP.STATIC_SOLID | GROUP.DYNAMIC | DEBRIS.group)), body);
    // It starts inside the car it came off: cars push it only once it has fallen clear (no depenetration kick).
    this.debris.set(spec.id, { body, until: this.tick + Math.round(DEBRIS.lifetimeS / DT), solidAt: this.tick + Math.round(.6 / DT),
      floor: spec.pos.y - 10, state: null });
  }
  private removeDebris(id: string) {
    const piece = this.debris.get(id);
    if (!piece) return;
    this.world.removeRigidBody(piece.body);
    this.debris.delete(id);
  }
  /** Debris poses; a resting piece repeats its last state object (no delta). */
  debrisStates(): DebrisState[] {
    for (const [id, piece] of this.debris) {
      // Gone after its lifetime, or at once if it fell out of the world (through a gap in the ground).
      if (this.tick >= piece.until || piece.body.translation().y < piece.floor) { this.removeDebris(id); continue; }
      if (this.tick >= piece.solidAt && piece.solidAt >= 0) {
        piece.body.collider(0).setCollisionGroups(groups(DEBRIS.group, GROUP.STATIC_SOLID | GROUP.VEHICLE | GROUP.DYNAMIC | DEBRIS.group));
        piece.solidAt = -1;
      }
    }
    return [...this.debris].map(([id, piece]) => {
      if (piece.state && piece.body.isSleeping()) return piece.state;
      const pos = piece.body.translation(), rot = piece.body.rotation();
      return piece.state = { id, pos: { x: pos.x, y: pos.y, z: pos.z }, rot: { x: rot.x, y: rot.y, z: rot.z, w: rot.w } };
    });
  }

  poleStates(brokenOnly = false): PoleState[] {
    return [...this.poles].filter(([, p]) => !brokenOnly || p.brokenAt !== null).map(([id, p]) => {
      const resting = p.body.isSleeping() || p.body.bodyType() === this.R.RigidBodyType.Fixed;
      const old = this.poleSnapshots.get(id);
      if (brokenOnly && old && resting && old.resting && !p.brokeNow) return old.state;
      const pos = p.body.translation(), rot = p.body.rotation();
      const state = { id, pos: { x: pos.x, y: pos.y, z: pos.z },
        rot: { x: rot.x, y: rot.y, z: rot.z, w: rot.w }, broken: p.brokenAt !== null, brokeNow: p.brokeNow };
      p.brokeNow = false;
      if (brokenOnly) this.poleSnapshots.set(id, { state, resting });
      return state;
    });
  }

  /**
   * Cells an actor at (x, z) may overlap without a hold. Resident cells, and, while the actor's own cell is resident,
   * cells with no collision source: an edge wall closes that border (edgeWalls.ts), so the body stops there. A car's
   * footprint circle (half-diagonal + 0.5 m) reaches past the wall, and capture 20261105 (shared car at 122,5,3 with
   * row j=-1 unpublished) reset the car every time it came within 3.1 m of that edge.
   */
  private supported(x: number, z: number): { has(id: string): boolean } {
    const c = collisionTileAt(x, z);
    const walled = this.residentCells.has(collisionTileId(c.i, c.j));
    return { has: id => this.residentCells.has(id) || (walled && !this.edges.isKnown(id)) };
  }

  /** Cells whose collision is on its way: actively loading, or known to exist (queued by the streamer). */
  private arriving(x: number, z: number): { has(id: string): boolean } {
    const supported = this.supported(x, z);
    return { has: id => supported.has(id) || this.loadingCells.has(id) || this.edges.isKnown(id) };
  }

  // ------------------------------------------------------------------ stepping
  /** Advances real time dt with fixed 60 Hz substeps. Returns the number of substeps run. */
  step(dt: number, character: CharacterInput, vehicle: VehicleInput | null): number {
    this.jumpPending ||= character.jump;
    this.acc = Math.min(this.acc + Math.max(0, dt), MAX_SUBSTEPS * DT);
    let n = 0, trafficMs = 0;
    const substeps = Math.min(MAX_SUBSTEPS, Math.floor((this.acc + 1e-9) / DT));
    while (this.acc >= DT - 1e-9 && n < MAX_SUBSTEPS) {
      // This decision belongs to the worker, immediately before every fixed substep: main-thread
      // braking cannot stop gravity, an evicted parked car, or a late input after a flight toggle.
      let safeCharacter: CharacterInput | null = { ...character, jump: this.jumpPending };
      if (character.fly) this.coverageHold.clear('character');
      if (this.charCollider && this.charEnabled && !character.fly) {
        const p = this.charCollider.translation();
        const r = CHARACTER.radius + CHARACTER.skin, nx = p.x + character.vx * DT, nz = p.z + character.vz * DT;
        const covered = collisionCovers(this.supported(p.x, p.z), p.x, p.z, nx, nz, r);
        if (!covered) {
          this.coverageHold.block('character', this.tick * DT, collisionCovers(this.arriving(p.x, p.z), p.x, p.z, nx, nz, r));
          safeCharacter = null;
          this.charVy = 0; this.charVel = { x: 0, y: 0, z: 0 }; this.charGrounded = false;
        } else this.coverageHold.clear('character');
      }
      for (const [id, car] of this.vehicles) {
        const held = this.heldVehicles.get(id);
        const p = car.body.translation(), v = held?.velocity ?? car.body.linvel();
        const radius = this.vehicleRadii.get(id)!;
        const nx = p.x + v.x * DT, nz = p.z + v.z * DT;
        const covered = collisionCovers(this.supported(p.x, p.z), p.x, p.z, nx, nz, radius);
        if (!covered && id === this.drivenId) {
          this.coverageHold.block(id, this.tick * DT, collisionCovers(this.arriving(p.x, p.z), p.x, p.z, nx, nz, radius));
        } else if (covered) this.coverageHold.clear(id);
        if (!covered && !held) {
          this.heldVehicles.set(id, { velocity: { ...v }, angular: { ...car.body.angvel() } });
          car.body.setLinvel({ x: 0, y: 0, z: 0 }, false);
          car.body.setAngvel({ x: 0, y: 0, z: 0 }, false);
          car.body.setBodyType(this.R.RigidBodyType.Fixed, false);
          this.vehicleSnapshots.delete(id);
        } else if (covered && this.heldVehicles.delete(id)) {
          car.body.setBodyType(this.R.RigidBodyType.Dynamic, true);
          car.body.setLinvel(held!.velocity, true);
          car.body.setAngvel(held!.angular, true);
          car.body.wakeUp();
          this.vehicleSnapshots.delete(id);
        }
      }
      const tt = performance.now();
      this.traffic?.beforeStep(this.trafficTime - (substeps - 1 - n) * DT);
      trafficMs += performance.now() - tt;
      this.fixedStep(safeCharacter, this.easeForCoverage(vehicle));
      const ta = performance.now();
      if (this.traffic) this.traffic.afterStep(this.poleEvents);
      trafficMs += performance.now() - ta;
      this.jumpPending = false;
      this.acc -= DT;
      n++;
    }
    this.lastTrafficMs = trafficMs;
    return n;
  }
  /** Ambient traffic's share of the latest step() (ms). */
  lastTrafficMs = 0;

  /** The driven car's input eased before collision that is not installed yet: a fast car slows smoothly and stops short
   *  of the gap (easedSpeed), keeping its steering, instead of reaching the gap's edge and being frozen there with its
   *  speed (the hold in step() still guards the edge). Also measures the time to that gap (coverageLead). */
  private easeForCoverage(vehicle: VehicleInput | null): VehicleInput | null {
    this.easing = null; this.coverageLead = Infinity;
    const id = this.drivenId, car = id && !this.charEnabled ? this.vehicles.get(id) : undefined;
    if (!vehicle || !car || this.heldVehicles.has(id!)) return vehicle;
    const v = car.body.linvel(), speed = Math.hypot(v.x, v.z), p = car.body.translation();
    const { hx, hz } = chassisForward(car.body.rotation()), forwardSpeed = v.x * hx + v.z * hz;
    // Moving: along the velocity. At rest: along the drive asked for (the brake pedal reverses at rest).
    const intent = vehicle.throttle > 0 ? 1 : vehicle.throttle < 0 || vehicle.brake > 0 ? -1 : 0;
    if (speed < EASE_REST_SPEED && !intent) return vehicle;
    const [dx, dz] = speed >= EASE_REST_SPEED ? [v.x / speed, v.z / speed] : [hx * intent, hz * intent];
    // Collision known but not installed: the swept circle the hold guards. A tile still coming keeps its edge wall,
    // but a wall at speed is a dead stop: the chassis box must stop short of it (driving along it is fine).
    const limit = Math.max(easeReachLimit(speed), COVERAGE_URGENT_S * speed);
    let reach = coverageReach(this.supported(p.x, p.z), p.x, p.z, dx, dz, this.vehicleRadii.get(id!)!, limit);
    if (this.coming.size) {
      const walled = { has: (cell: string) => this.coming.has(cell) && !this.residentCells.has(cell) };
      const box = { ux: hx, uz: hz, a: car.halfExt.x + BODY_MARGIN, b: car.halfExt.z + BODY_MARGIN };
      reach = Math.min(reach, bodyReach(walled, p.x, p.z, dx, dz, box, limit));
    }
    if (reach === Infinity) return vehicle;
    this.coverageLead = reach / Math.max(speed, 1);
    const allowed = easedSpeed(reach);
    if (speed >= EASE_ROLL_SPEED) {
      const eased = easeInput(vehicle, forwardSpeed, speed, allowed);
      if (eased !== vehicle) this.easing = allowed;
      return eased;
    }
    if (allowed > EASE_ROLL_SPEED) return vehicle;
    // Creeping at the gap: no drive towards it, the handbrake holds the car unless the driver drives away.
    this.easing = allowed;
    if (dx * hx + dz * hz >= 0) {
      const away = vehicle.brake > 0 || vehicle.throttle < 0;
      return { ...vehicle, throttle: Math.min(0, vehicle.throttle), handbrake: vehicle.handbrake || !away };
    }
    return { ...vehicle, brake: 0, throttle: Math.max(0, vehicle.throttle), handbrake: vehicle.handbrake || vehicle.throttle <= 0 };
  }

  /** Low-level solver step, also used by standalone synthetic collider fixtures. Runtime uses step(). */
  fixedStep(character: CharacterInput | null, vehicle: VehicleInput | null) {
    this.previousCharacter = this.characterState();
    this.previousVehicle = this.vehicleState();
    this.tick++;
    if (character && this.cc && this.charEnabled) this.stepCharacter(character);
    if (this.charEnabled && this.charCollider) {
      const feet = this.charCollider.translation();
      for (const [id, car] of this.vehicles) {
        const pos = car.body.translation();
        const distance = Math.hypot(pos.x - feet.x, pos.z - feet.z);
        if (distance < 6 && !this.nearbyVehicles.has(id)) {
          this.nearbyVehicles.add(id);
          car.body.wakeUp();
        } else if (distance > 8) this.nearbyVehicles.delete(id);
      }
    }
    const carSpeeds = new Map<number, { speed: number; local: boolean }>(), carIds = new Map<number, string>();
    this.applyImpulses();
    for (const [id, car] of this.vehicles) {
      if (!this.heldVehicles.has(id)) car.step(DT, id === this.drivenId ? vehicle : null);
      const v = car.body.linvel();
      carSpeeds.set(car.body.handle, { speed: Math.hypot(v.x, v.z), local: id === this.drivenId });
      carIds.set(car.body.handle, id);
    }
    this.sweepRemotes();
    this.driveRemoteCars();
    stepPipeline(this.world, this.poleEvents, this.traffic?.hooks);
    if (this.remotes.size) this.noteRemoteContacts(carIds);
    if (this.remoteCars.size) this.noteRemoteCarContacts(carIds);
    for (const [id, car] of this.vehicles) if (!this.heldVehicles.has(id)) car.afterStep(DT);
    for (const car of this.remoteCars.values()) car.vehicle.afterStep(DT);
    const poleColliders = new Map<number, string>();
    for (const [id, pole] of this.poles) if (pole.brokenAt === null) poleColliders.set(pole.body.collider(0).handle, id);
    this.poleEvents.drainContactForceEvents(event => {
      const a = event.collider1(), b = event.collider2();
      const id = poleColliders.get(a) ?? poleColliders.get(b);
      if (!id || event.totalForceMagnitude() * DT < 1) return;
      const other = this.world.getCollider(poleColliders.has(a) ? b : a);
      const car = other?.parent() && carSpeeds.get(other.parent()!.handle);
      if (car && car.speed >= 10 / 3.6) this.breakPole(id, car.local);
    });
    for (const pole of this.poles.values()) {
      if (pole.brokenAt !== null) {
        if (this.tick - pole.brokenAt >= 20 / DT && pole.body.bodyType() !== this.R.RigidBodyType.Fixed)
          pole.body.setBodyType(this.R.RigidBodyType.Fixed, true);
        continue;
      }
    }
  }

  // ------------------------------------------------------------------ queries
  cameraCast(origin: V3, dir: V3, distance: number): number {
    const hit = this.world.castShape(origin, { x: 0, y: 0, z: 0, w: 1 }, dir,
      new this.R.Ball(0.3), 0, distance, true, this.R.QueryFilterFlags.EXCLUDE_SENSORS,
      groups(0xff00, GROUP.STATIC_SOLID));
    return hit ? Math.max(0, hit.time_of_impact - 0.03) : distance;
  }

  raycast(origin: V3, dir: V3, maxToi: number, filter: RayFilter = 'solid'): RayHit | null {
    // Treads are query-only and can be below the KCC slope: prefer them over the solid ramp.
    if (filter === 'foot') {
      const tread = this.raycast(origin, dir, maxToi, 'query');
      if (tread?.material === MAT.stair_tread_helper) return tread;
      return this.raycast(origin, dir, maxToi, 'solid');
    }
    const R = this.R;
    const l = Math.hypot(dir.x, dir.y, dir.z) || 1;
    const d = { x: dir.x / l, y: dir.y / l, z: dir.z / l };
    const ray = new R.Ray(origin, d);
    const mask = filter === 'solid' ? GROUP.STATIC_SOLID : filter === 'query' ? GROUP.STATIC_QUERY : filter === 'water' ? GROUP.STATIC_SENSOR
      : GROUP.STATIC_SOLID | GROUP.STATIC_QUERY | GROUP.VEHICLE;
    const flags = filter === 'water' ? R.QueryFilterFlags.EXCLUDE_SOLIDS : R.QueryFilterFlags.EXCLUDE_SENSORS;
    const hit = this.world.castRayAndGetNormal(ray, maxToi, true, flags, groups(0xff00, mask), this.charCollider ?? undefined);
    if (!hit) return null;
    const toi = hit.timeOfImpact;
    const point = { x: origin.x + d.x * toi, y: origin.y + d.y * toi, z: origin.z + d.z * toi };
    return { toi, point, normal: { x: hit.normal.x, y: hit.normal.y, z: hit.normal.z },
      material: this.materialAt(hit.collider, point), tile: this.colliderInfo.get(hit.collider.handle)?.tile ?? null };
  }

  /** What a vehicle's chassis touches now: each collider in contact with the contact point, its tile cell and
   *  material, or the prop pole's id. For tools that explain an impact (the drive sweep's DRIVE_DEBUG). */
  vehicleContacts(id = 'player'): { tile: string | null; material: number; pole: string | null; point: V3 }[] {
    const vehicle = this.vehicles.get(id);
    if (!vehicle) return [];
    const out: { tile: string | null; material: number; pole: string | null; point: V3 }[] = [];
    for (let i = 0; i < vehicle.body.numColliders(); i++) {
      const own = vehicle.body.collider(i);
      this.world.contactPairsWith(own, other => {
        let point: V3 | null = null;
        this.world.contactPair(own, other, (manifold, flipped) => {
          if (point || manifold.numSolverContacts() === 0) return;
          const p = manifold.solverContactPoint(0);
          if (p) point = { x: p.x, y: p.y, z: p.z };
          void flipped;
        });
        if (!point) return;
        let pole: string | null = null;
        for (const [poleId, entry] of this.poles) if (entry.body.handle === other.parent()?.handle) pole = poleId;
        out.push({ tile: this.colliderInfo.get(other.handle)?.tile ?? null, material: this.materialAt(other, point), pole, point });
      });
    }
    return out;
  }

  private materialAt(collider: RAPIER_NS.Collider, point: V3): number {
    const info = this.colliderInfo.get(collider.handle);
    if (info?.triMat && info.locator) {
      const t = info.locator.locate(point.x, point.y, point.z);
      if (t >= 0) return info.triMat[t];
    }
    return info?.mat ?? 0;
  }

  totals() {
    let tris = 0, bytes = 0, curbTris = 0;
    for (const t of this.tiles.values()) { tris += t.stats.tris; bytes += t.stats.bytes; curbTris += t.stats.curbTris ?? 0; }
    return { tris, colBytes: bytes, estWasmBytes: (tris + curbTris) * 135 }; // ~135 B/tri measured (engine.md 2.2)
  }
}

const cellArea = (i: number, j: number) => [i * COLLISION_TILE, j * COLLISION_TILE, (i + 1) * COLLISION_TILE, (j + 1) * COLLISION_TILE] as const;

/** A player's vehicle as ambient drivers see it: chassis centre, forward (+X of the chassis) and footprint. */
function trafficVehicle(id: string, pos: V3, rot: Quat, vx: number, vz: number, half: V3): TrafficVehicle {
  return { id, x: pos.x, y: pos.y, z: pos.z, ...chassisForward(rot), vx, vz, length: 2 * half.x, width: 2 * half.z };
}
