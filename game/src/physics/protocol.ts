// Main thread <-> physics worker message protocol. All coordinates are engine world-local metres
// (origin O, Y-up); .col tiles carry their own float64 origin which becomes the collider translation.
import type { V3 } from '../core/origin';
import type { VehicleKind, VehicleTuning } from './constants';

export interface Quat { x: number; y: number; z: number; w: number }

export interface CharacterInput {
  /** Desired horizontal velocity (m/s), world axes. y is ignored. */
  vx: number; vz: number;
  jump: boolean;
  /** Developer fly mode (gameplay/flyMode.ts): no gravity, jump or step-up; the character moves with `vy` (m/s)
   *  vertically, keeping real collisions but ignoring playable-area edge walls. Absent: walking. */
  fly?: { vy: number };
}
/** `rearMode`: the rear-steer mode to hold from now on (the vehicle persists it like `lights`); only a 4WS vehicle
 *  reads it. */
export interface VehicleInput { throttle: number; steer: number; brake: number; handbrake: boolean; lights?: import('../vehicle/signals').LightState; horn?: boolean;
  rearMode?: import('../vehicle/rearSteer').RearSteerMode;
  /** `steer` comes from digital keys (−1, 0, 1): a car with `keySteer` tuning slews it (vehicle/vehicle.ts). */
  steerKey?: boolean }

/** `water`: only the water sensor surfaces (the river), which every other filter passes through. */
export type RayFilter = 'solid' | 'query' | 'all' | 'foot' | 'water';
export interface RayHit { toi: number; point: V3; normal: V3; material: number; tile: string | null }

export interface CharacterState { pos: V3; vel: V3; grounded: boolean; enabled: boolean; groundMaterial: number }
/** Contact point and normal are chassis-local, so replicated effects follow the interpolated pose. */
export interface TyreContact { point: V3; normal: V3; material: number; lateral: number; longitudinal: number }
export interface WheelState { tyre?: TyreContact; pos: V3; steer: number; rotation: number; contact: boolean; suspension: number }
export interface CurbStrikeState { id: number; wheel: number; height: number; speed: number; loss: number; blocked: boolean; rumble: number }
/** The latest wheel bump (vehicle/bumps.ts), with a monotonically increasing id: `height` is the step in the wheel's
 *  ground path (m, + up, - down; 0 when only the suspension jolted), `jolt` the peak suspension compression speed
 *  (m/s; 0 below the floor), `speed` the horizontal vehicle speed (m/s). */
export interface BumpState { id: number; wheel: number; height: number; jolt: number; speed: number }
export interface VehicleState { lights?: import('../vehicle/signals').LightState; horn?: boolean; id?: string; kind?: VehicleKind; impact?: { id: number; impulse: number } | null; bump?: BumpState | null; curbStrike: CurbStrikeState | null; pos: V3; rot: Quat; linvel: V3; speed: number; forwardSpeed: number; wheels: WheelState[];
  /** 4WS vehicles: the held rear-steer mode and whether the speed lockout centres the rear now. */
  rearMode?: import('../vehicle/rearSteer').RearSteerMode; rearLocked?: boolean;
  /** The body sleeps (its state is the one it fell asleep with) / is held Fixed until its collision arrives. */
  asleep?: boolean; held?: boolean;
  /** The driven car is eased down to this speed (m/s) before collision that has not arrived (physics/collisionCoverage.ts). */
  eased?: number;
  /** Drive command after handling (0..1 forward or reverse, 0 while braking), for engine load. */
  throttleApplied?: number;
  /** Body damage of a damageable car (vehicle/damage.ts encodeDamage), absent while it is undamaged. */
  damage?: string;
  /** A damageable car's body sliding along something: the fastest contact's sliding speed (m/s), absent when none. */
  scrape?: number;
  /** Chassis angular velocity (rad/s, world frame). */
  angvel?: V3;
  /** The driver input of this step, absent for an undriven car: another player's client replays it (net/motion.ts). */
  controls?: VehicleInput }
export interface VehicleSpec { rot?: Quat; linvel?: V3; id: string; kind?: VehicleKind; pos: V3; yaw: number; mass?: number; halfExt?: V3; parked?: boolean;
  /** Per-model driving tuning (vehicle/modelTuning.ts), replacing the kind's baseline. */
  tuning?: VehicleTuning;
  /** A damageable car: the fleet-frame box its damage cage spans (actors/fleet/catalog.ts fleetDamageBox). Saved or
   *  shared damage arrives with `vehicleDamage`. */
  damage?: { box: import('../vehicle/damage').DamageBox } }
/** `vel`: the owner's reported velocity (net pose vE/vH/-vN); the proxy is swept towards `pos` no faster (world.ts). */
export interface RemoteBody { rot?: Quat; id: string; pos: V3; yaw: number; mode: 'foot' | VehicleKind; vel?: V3;
  /** A car's newest owner state (net/motion.ts), for the full simulation of nearby remote cars (world.ts REMOTE_SIM). */
  state?: RemoteCarState;
  /** Its model and the model's driving tuning: the tuning is resent now and then, the worker keeps the last one. */
  model?: string; tuning?: VehicleTuning | null;
  /** This client is the authority of this car's contacts with its own car (world.ts REMOTE_SIM). */
  lead?: boolean }
/** `st`: room-clock ms of the state; `age`: seconds it was old when sent; `pos`: chassis centre; `input`: the owner's
 *  replicated controls (null: undriven). */
export interface RemoteCarState { st: number; age: number; pos: V3; rot: Quat; vel: V3; spin: V3; input: VehicleInput | null }
/** A remote car simulated here: chassis centre, rotation, velocity and how often it was placed on its owner's state;
 *  `held` while this client is the authority of its contact (world.ts REMOTE_SIM), with its velocity and spin. */
export interface RemoteCarDraw { id: string; pos: V3; rot: Quat; vel: V3; snaps: number; held?: { vel: V3; spin: V3 } }
export interface PoleSpec { id: string; base: V3; yaw: number; height: number }
export interface PoleState { id: string; pos: V3; rot: Quat; broken: boolean; brokeNow: boolean }
/** A car part that came off (actors/fleet/debris.ts): a box body of `half` extents and `mass` (kg) at its drawn pose. */
export interface DebrisSpec { id: string; pos: V3; rot: Quat; half: V3; mass: number; linvel: V3; angvel: V3 }
export interface DebrisState { id: string; pos: V3; rot: Quat }

/** `buildMs`: worker time spent building the cell; `pieces`: its install pieces; `waitMs`: request to install, queue included. */
export interface TileStats { curbTris?: number; id: string; tris: number; prims: number; bytes: number; buildMs: number; colliders: number; pieces?: number; waitMs?: number }

/** A late physics snapshot, as the worker saw it: `gap` (no tick for `ms`; `busy` lists the message handling and
 *  installs that ran in between, ms by kind), `step` (one snapshot's fixed substeps took `ms`; `traffic` of it in
 *  the ambient traffic sim), or `install` (one collider piece of cell `cell` took `ms`). `wall` is the worker's
 *  epoch time (ms) of the event. Repeated in snapshots until acknowledged, like traffic claim events. */
export interface PhysStall { id: number; revision: number; cause: 'gap' | 'step' | 'install'; ms: number; wall: number;
  busy?: Record<string, number>; substeps?: number; traffic?: number; cell?: string; phase?: string; queued?: number }

/** Ambient traffic (physics/traffic.ts). A tile's road graph as CoarseTiles holds it, with its agreed hash. */
export interface TrafficTile { tile: string; hash: string; bounds: [number, number, number, number]; roads: unknown }
/** The clock the traffic density follows: the shared day cycle (with ?cycle='s period in single player) or a frozen
 *  clock hour (?time=, the developer clock, also while it runs; single player only). */
export type TrafficClock = { kind: 'cycle'; periodMs: number } | { kind: 'hour'; hour: number };
/** `enabled`: tiles whose lanes may carry cars (null: all); `claims`: slot keys other clients claimed; `density`:
 *  multiplier on every street's flow (?trafficDensity=, 1 by default); `clock`: see TrafficClock (multiplayer
 *  always follows the shared cycle). */
export interface TrafficConfig { cap: number; offsetMs: number; shared: boolean; enabled: string[] | null; claims: string[];
  density: number; clock: TrafficClock;
  /** Multiplayer: the server's traffic watch (server/watch.js), applied at the same tick on every client. */
  watch?: { at: number; points: [number, number][] };
  /** Multiplayer traffic authority (server/roads.js): shared tiles this client owns (it sends their cars,
   *  `trafficSnapshot`) and tiles another client owns (no cars enter at their sources here; their cars come from
   *  the owner's snapshots, `trafficMirror`). */
  owned?: string[]; remote?: string[] }
/** A car the player touched or entered became the dynamic vehicle `id` (a ParkedVehicle-like record). */
export interface TrafficClaimEvent { revision: number; handle?: number; id: string; key: string; seed?: string; model: string; color: string;
  size: [number, number, number]; pos: V3; rot: Quat; linvel: V3 }
/** Camera for the population ring: position, unit horizontal forward and horizontal half field of view. */
export interface TrafficView { x: number; z: number; fx: number; fz: number; halfFov: number }
/** `hour`: the clock hour the density follows now; `deferred`: drawn cars whose slot no longer carries a car (they
 *  go once out of view). */
export interface TrafficStats { active: number; bodies: number; lanes: number; tiles: number; stepMs: number; stepMax: number; activationMs: number;
  claims: number; hour: number; deferred: number; mirrored: number }

/** Jump is an edge counter, so a pulse survives several render frames between worker reads. */
export interface LiveInput { character: CharacterInput; vehicle: VehicleInput | null; running: boolean; jump: number; at: number; acknowledged: number; view?: TrafficView }
export type PhysicsState = Extract<FromWorker, { type: 'state' }>;

export type ToWorker =
  /** Resets the world (the worker initialises itself on load and posts 'ready'). */
  | { type: 'init' }
  | { type: 'exchange'; input: SharedArrayBuffer; state: SharedArrayBuffer }
  | { type: 'input'; input: LiveInput }
  | { type: 'stateAck'; revision: number }
  | { type: 'loadTile'; id: string; buffer: ArrayBuffer }
  | { type: 'unloadTile'; id: string }
  /** A known collision payload is actively fetching/being installed, not queued or retrying. */
  | { type: 'loadingTile'; id: string; loading: boolean }
  /** Collision tiles that exist (published or draft) but may not be loaded: no edge wall faces them (edgeWalls.ts). */
  | { type: 'knownTiles'; add: string[]; remove: string[] }
  | { type: 'comingTiles'; add: string[]; remove: string[] }
  /** Decode a tile without adding it; answered with 'checkResult'. */
  | { type: 'checkTile'; id: number; buffer: ArrayBuffer }
  | { type: 'spawnCharacter'; pos: V3 }
  | { type: 'setCharacterEnabled'; enabled: boolean; pos?: V3 }
  | { type: 'removeVehicle'; id: string }
  | { type: 'spawnVehicle'; spec: VehicleSpec }
  | { type: 'driveVehicle'; id: string | null }
  | { type: 'resetVehicle'; id: string; pos: V3; yaw: number }
  | { type: 'vehicleImpulse'; id: string; impulse: V3 }
  /** A hit on a damageable car's body, applied like a crash (developer tools; vehicle/damage.ts DamageHit). */
  | { type: 'damageVehicle'; id: string; hit: import('../vehicle/damage').DamageHit }
  /** Developer tools: `metres` of scraping along a damageable car's `face` from `from` to `to` (fleet frame). */
  | { type: 'scrapeVehicle'; id: string; scrape: import('../vehicle/damage').DamageScrape }
  /** Sets a damageable car's body damage: a saved or shared state (vehicle/damage.ts encodeDamage), or null for a
   *  fresh body (a new game, a progress reset). The game has no repair. */
  | { type: 'vehicleDamage'; id: string; state: string | null }
  /** This client owns vehicle `id` (its shared record): other players' cars damage it here too (vehicle.ts ownDamage). */
  | { type: 'ownVehicleDamage'; id: string }
  | { type: 'remotes'; bodies: RemoteBody[] }
  | { type: 'follow'; id: string; state: RemoteCarState | null }
  | { type: 'poles'; add: PoleSpec[]; remove: string[] }
  | { type: 'breakPole'; id: string }
  | { type: 'spawnDebris'; spec: DebrisSpec }
  | { type: 'traffic'; config: Partial<TrafficConfig> }
  | { type: 'trafficTiles'; add: TrafficTile[]; remove: string[] }
  | { type: 'trafficPedestrians'; time: number; people: import('../traffic/pedestrians').TrafficPedestrian[] }
  /** Claim an ambient car for entering it; answered by its claim event (with `handle`) in the state. */
  | { type: 'trafficClaim'; handle: number }
  | { type: 'trafficMirror'; snapshot: import('../traffic/flow').TrafficSnapshot }
  /** Advance by dt seconds of real time (worker runs fixed 60 Hz substeps, max 5 per message). */
  | { type: 'step'; seq: number; dt: number; character: CharacterInput; vehicle: VehicleInput | null }
  | { type: 'cameraCast'; id: number; origin: V3; dir: V3; distance: number }
  | { type: 'raycast'; id: number; rays: { origin: V3; dir: V3; maxToi: number; filter: RayFilter }[] }
  | { type: 'stats'; id: number };

export type FromWorker =
  | { type: 'ready'; rapier: string; colReader: string; wasmBytes?: number }
  | { type: 'tileLoaded'; stats: TileStats }
  | { type: 'tileError'; id: string; error: string; permanent: boolean }
  | { type: 'tileUnloaded'; id: string }
  /** The cars on this client's owned tiles (TrafficConfig.owned), every TRAFFIC.mirrorTicks flow ticks. */
  | { type: 'trafficSnapshot'; snapshot: import('../traffic/flow').TrafficSnapshot }
  | { type: 'checkResult'; id: number; tris: number; error: string | null }
  | { type: 'state'; coverageBlock?: { actor: string; recover: boolean } | null; publishedAt?: number; revision?: number; actorsDelta?: boolean;
      /** `vehicles` leaves out `vehicle` (sent once); the receiver puts it into its list. */
      vehicleApart?: boolean; removedVehicles?: string[]; removedPoles?: string[]; seq: number; substeps: number; stepMs: number; tick: number; remainder: number; previousCharacter: CharacterState | null; character: CharacterState | null; previousVehicle: VehicleState | null; vehicle: VehicleState | null; vehicles: VehicleState[]; poles: PoleState[];
      /** Car part debris bodies (deltas like the poles). */
      debris?: DebrisState[]; removedDebris?: string[];
      /** Drawn ambient cars, physics/traffic.ts TRAFFIC_STRIDE numbers each; claim events until acknowledged. */
      traffic?: number[]; trafficEvents?: TrafficClaimEvent[]; trafficStats?: TrafficStats; trafficTime?: number;
      /** Remote cars a local hit moved: drawn at these poses (network frame: H at the drawn root) until back on their
       *  owner's pose (world.ts REMOTE_SERVO). */
      remoteProxies?: { id: string; pos: V3; rot: Quat }[];
      remoteCars?: RemoteCarDraw[];
      /** Worker stalls not yet acknowledged (PhysStall). */
      stalls?: PhysStall[] }
  | { type: 'cameraCastResult'; id: number; distance: number }
  | { type: 'raycastResult'; id: number; hits: (RayHit | null)[] }
  | { type: 'statsResult'; id: number; tiles: TileStats[]; tris: number; colBytes: number; estWasmBytes: number }
  | { type: 'stepError'; seq: number; error: string }
  | { type: 'error'; error: string };
