// Main thread <-> physics worker message protocol. All coordinates are engine world-local metres
// (origin O, Y-up); .col tiles carry their own float64 origin which becomes the collider translation.
import type { V3 } from '../core/origin';
import type { VehicleKind } from './constants';

export interface Quat { x: number; y: number; z: number; w: number }

export interface CharacterInput {
  /** Desired horizontal velocity (m/s), world axes. y is ignored. */
  vx: number; vz: number;
  jump: boolean;
  /** Developer fly mode (gameplay/flyMode.ts): no gravity, jump or step-up; the character moves with `vy` (m/s)
   *  vertically, keeping real collisions but ignoring playable-area edge walls. Absent: walking. */
  fly?: { vy: number };
}
export interface VehicleInput { throttle: number; steer: number; brake: number; handbrake: boolean }

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
export interface VehicleState { id?: string; kind?: VehicleKind; impact?: { id: number; impulse: number } | null; bump?: BumpState | null; curbStrike: CurbStrikeState | null; pos: V3; rot: Quat; linvel: V3; speed: number; forwardSpeed: number; wheels: WheelState[] }
export interface VehicleSpec { rot?: Quat; linvel?: V3; id: string; kind?: VehicleKind; pos: V3; yaw: number; mass?: number; halfExt?: V3; parked?: boolean }
export interface RemoteBody { rot?: Quat; id: string; pos: V3; yaw: number; mode: 'foot' | VehicleKind }
export interface PoleSpec { id: string; base: V3; yaw: number; height: number }
export interface PoleState { id: string; pos: V3; rot: Quat; broken: boolean; brokeNow: boolean }

export interface TileStats { curbTris?: number; id: string; tris: number; prims: number; bytes: number; buildMs: number; colliders: number }

/** Ambient traffic (physics/traffic.ts). A tile's road graph as CoarseTiles holds it, with its agreed hash. */
export interface TrafficTile { tile: string; hash: string; bounds: [number, number, number, number]; roads: unknown }
/** The clock the traffic density follows: the shared day cycle (with ?cycle='s period in single player) or a frozen
 *  clock hour (?time=, the developer clock, also while it runs; single player only). */
export type TrafficClock = { kind: 'cycle'; periodMs: number } | { kind: 'hour'; hour: number };
/** `enabled`: tiles whose lanes may carry cars (null: all); `claims`: slot keys other clients claimed; `density`:
 *  multiplier on every street's flow (?trafficDensity=, 1 by default); `clock`: see TrafficClock (multiplayer
 *  always follows the shared cycle). */
export interface TrafficConfig { cap: number; offsetMs: number; shared: boolean; enabled: string[] | null; claims: string[];
  density: number; clock: TrafficClock }
/** A car the player touched or entered became the dynamic vehicle `id` (a ParkedVehicle-like record). */
export interface TrafficClaimEvent { revision: number; handle?: number; id: string; key: string; seed?: string; model: string; color: string;
  size: [number, number, number]; pos: V3; rot: Quat; linvel: V3 }
/** Camera for the population ring: position, unit horizontal forward and horizontal half field of view. */
export interface TrafficView { x: number; z: number; fx: number; fz: number; halfFov: number }
/** `hour`: the clock hour the density follows now; `deferred`: drawn cars whose slot no longer carries a car (they
 *  go once out of view). */
export interface TrafficStats { active: number; bodies: number; lanes: number; tiles: number; stepMs: number; stepMax: number; activationMs: number;
  claims: number; hour: number; deferred: number }

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
  /** Decode a tile without adding it; answered with 'checkResult'. */
  | { type: 'checkTile'; id: number; buffer: ArrayBuffer }
  | { type: 'spawnCharacter'; pos: V3 }
  | { type: 'setCharacterEnabled'; enabled: boolean; pos?: V3 }
  | { type: 'removeVehicle'; id: string }
  | { type: 'spawnVehicle'; spec: VehicleSpec }
  | { type: 'driveVehicle'; id: string | null }
  | { type: 'resetVehicle'; id: string; pos: V3; yaw: number }
  | { type: 'vehicleImpulse'; id: string; impulse: V3 }
  | { type: 'remotes'; bodies: RemoteBody[] }
  | { type: 'poles'; add: PoleSpec[]; remove: string[] }
  | { type: 'breakPole'; id: string }
  | { type: 'traffic'; config: Partial<TrafficConfig> }
  | { type: 'trafficTiles'; add: TrafficTile[]; remove: string[] }
  /** Claim an ambient car for entering it; answered by its claim event (with `handle`) in the state. */
  | { type: 'trafficClaim'; handle: number }
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
  | { type: 'checkResult'; id: number; tris: number; error: string | null }
  | { type: 'state'; coverageBlock?: { actor: string; recover: boolean } | null; publishedAt?: number; revision?: number; actorsDelta?: boolean; removedVehicles?: string[]; removedPoles?: string[]; seq: number; substeps: number; stepMs: number; tick: number; remainder: number; previousCharacter: CharacterState | null; character: CharacterState | null; previousVehicle: VehicleState | null; vehicle: VehicleState | null; vehicles: VehicleState[]; poles: PoleState[];
      /** Drawn ambient cars, physics/traffic.ts TRAFFIC_STRIDE numbers each; claim events until acknowledged. */
      traffic?: number[]; trafficEvents?: TrafficClaimEvent[]; trafficStats?: TrafficStats; trafficTime?: number }
  | { type: 'cameraCastResult'; id: number; distance: number }
  | { type: 'raycastResult'; id: number; hits: (RayHit | null)[] }
  | { type: 'statsResult'; id: number; tiles: TileStats[]; tris: number; colBytes: number; estWasmBytes: number }
  | { type: 'stepError'; seq: number; error: string }
  | { type: 'error'; error: string };
