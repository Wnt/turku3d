// Main thread <-> physics worker message protocol. All coordinates are engine world-local metres
// (origin O, Y-up); .col tiles carry their own float64 origin which becomes the collider translation.
import type { V3 } from '../core/origin';

export interface Quat { x: number; y: number; z: number; w: number }

export interface CharacterInput {
  /** Desired horizontal velocity (m/s), world axes. y is ignored. */
  vx: number; vz: number;
  jump: boolean;
}
export interface VehicleInput { throttle: number; steer: number; brake: number; handbrake: boolean }

export type RayFilter = 'solid' | 'query' | 'all' | 'foot';
export interface RayHit { toi: number; point: V3; normal: V3; material: number; tile: string | null }

export interface CharacterState { pos: V3; vel: V3; grounded: boolean; enabled: boolean; groundMaterial: number }
export interface WheelState { pos: V3; steer: number; rotation: number; contact: boolean; suspension: number }
export interface CurbStrikeState { id: number; wheel: number; height: number; speed: number; loss: number; blocked: boolean; rumble: number }
export interface VehicleState { curbStrike: CurbStrikeState | null; pos: V3; rot: Quat; linvel: V3; speed: number; forwardSpeed: number; wheels: WheelState[] }

export interface TileStats { curbTris?: number; id: string; tris: number; prims: number; bytes: number; buildMs: number; colliders: number }

export type ToWorker =
  /** Resets the world (the worker initialises itself on load and posts 'ready'). */
  | { type: 'init' }
  | { type: 'loadTile'; id: string; buffer: ArrayBuffer }
  | { type: 'unloadTile'; id: string }
  | { type: 'spawnCharacter'; pos: V3 }
  | { type: 'setCharacterEnabled'; enabled: boolean; pos?: V3 }
  | { type: 'spawnVehicle'; pos: V3; yaw: number }
  | { type: 'resetVehicle'; pos: V3; yaw: number }
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
  | { type: 'state'; seq: number; substeps: number; stepMs: number; tick: number; remainder: number; previousCharacter: CharacterState | null; character: CharacterState | null; vehicle: VehicleState | null }
  | { type: 'cameraCastResult'; id: number; distance: number }
  | { type: 'raycastResult'; id: number; hit: RayHit | null }
  | { type: 'statsResult'; id: number; tiles: TileStats[]; tris: number; colBytes: number; estWasmBytes: number }
  | { type: 'stepError'; seq: number; error: string }
  | { type: 'error'; error: string };
