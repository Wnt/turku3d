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
   *  vertically and keeps its collisions. Absent: walking. */
  fly?: { vy: number };
}
export interface VehicleInput { throttle: number; steer: number; brake: number; handbrake: boolean }

/** `water`: only the water sensor surfaces (the river), which every other filter passes through. */
export type RayFilter = 'solid' | 'query' | 'all' | 'foot' | 'water';
export interface RayHit { toi: number; point: V3; normal: V3; material: number; tile: string | null }

export interface CharacterState { pos: V3; vel: V3; grounded: boolean; enabled: boolean; groundMaterial: number }
export interface WheelState { pos: V3; steer: number; rotation: number; contact: boolean; suspension: number }
export interface CurbStrikeState { id: number; wheel: number; height: number; speed: number; loss: number; blocked: boolean; rumble: number }
export interface VehicleState { id?: string; kind?: VehicleKind; impact?: { id: number; impulse: number } | null; curbStrike: CurbStrikeState | null; pos: V3; rot: Quat; linvel: V3; speed: number; forwardSpeed: number; wheels: WheelState[] }
export interface VehicleSpec { id: string; kind?: VehicleKind; pos: V3; yaw: number; mass?: number; halfExt?: V3; parked?: boolean }
export interface RemoteBody { id: string; pos: V3; yaw: number; mode: 'foot' | 'car' | 'scooter' }

export interface TileStats { curbTris?: number; id: string; tris: number; prims: number; bytes: number; buildMs: number; colliders: number }

export type ToWorker =
  /** Resets the world (the worker initialises itself on load and posts 'ready'). */
  | { type: 'init' }
  | { type: 'loadTile'; id: string; buffer: ArrayBuffer }
  | { type: 'unloadTile'; id: string }
  /** Decode a tile without adding it; answered with 'checkResult'. */
  | { type: 'checkTile'; id: number; buffer: ArrayBuffer }
  | { type: 'spawnCharacter'; pos: V3 }
  | { type: 'setCharacterEnabled'; enabled: boolean; pos?: V3 }
  | { type: 'spawnVehicle'; spec: VehicleSpec }
  | { type: 'driveVehicle'; id: string | null }
  | { type: 'resetVehicle'; id: string; pos: V3; yaw: number }
  | { type: 'remotes'; bodies: RemoteBody[] }
  /** Advance by dt seconds of real time (worker runs fixed 60 Hz substeps, max 5 per message). */
  | { type: 'step'; seq: number; dt: number; character: CharacterInput; vehicle: VehicleInput | null }
  | { type: 'cameraCast'; id: number; origin: V3; dir: V3; distance: number }
  | { type: 'raycast'; id: number; origin: V3; dir: V3; maxToi: number; filter: RayFilter }
  | { type: 'stats'; id: number };

export type FromWorker =
  | { type: 'ready'; rapier: string; colReader: string }
  | { type: 'tileLoaded'; stats: TileStats }
  | { type: 'tileError'; id: string; error: string; permanent: boolean }
  | { type: 'tileUnloaded'; id: string }
  | { type: 'checkResult'; id: number; tris: number; error: string | null }
  | { type: 'state'; seq: number; substeps: number; stepMs: number; tick: number; remainder: number; previousCharacter: CharacterState | null; character: CharacterState | null; previousVehicle: VehicleState | null; vehicle: VehicleState | null; vehicles: VehicleState[] }
  | { type: 'cameraCastResult'; id: number; distance: number }
  | { type: 'raycastResult'; id: number; hit: RayHit | null }
  | { type: 'statsResult'; id: number; tiles: TileStats[]; tris: number; colBytes: number; estWasmBytes: number }
  | { type: 'stepError'; seq: number; error: string }
  | { type: 'error'; error: string };
