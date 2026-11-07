import { enToEngine, type V3 } from '../core/origin';
import type { SaveGame } from '../core/save';
import room from '../../../server/spawns.json';

/** Half size (m) of the box around each of the Turku room's spawn slots that the start car keeps out of. */
export const ROOM_SLOT_M = 1.2;
/** The Turku room's spawn slots (server/spawns.json: E = engine x, N = -engine z) as boxes the own car never parks
 *  on (world/startGround.ts obstacles): a player joining the room stands on one, never in or on the car. */
/** Distinct start-car spots the players of a room cycle through (by room slot): the search for more would reach too far
 *  from the start; with more players at once than this, two may share one again. */
export const ROOM_START_CARS = 8;
/** The index of the room's spawn slot `spawn` (server/spawns.json order; the room hands them out in turn to joining
 *  players, in every city), or 0. */
export function roomSlot(spawn?: { E: number; N: number } | null) {
  const i = spawn ? room.points.findIndex(p => Math.abs(p.E - spawn.E) < 0.01 && Math.abs(p.N - spawn.N) < 0.01) : -1;
  return Math.max(0, i);
}
export function roomSlotFootprints() {
  return room.points.map(p => ({ minX: p.E - ROOM_SLOT_M, maxX: p.E + ROOM_SLOT_M, minZ: -p.N - ROOM_SLOT_M, maxZ: -p.N + ROOM_SLOT_M }));
}

/** Multiplayer uses local E/N; saves use EPSG:3877. Keep that conversion at this boundary. */
export function multiplayerStart(you: { spawn: { E: number; N: number; yaw: number } },
  self?: { pose: { E: number; N: number; H: number; yaw: number } | null },
  seat?: { pose: { E: number; N: number; H: number; yaw: number } },
  start?: { x: number; y: number; z: number; yaw: number }) {
  const pose = seat?.pose ?? self?.pose;
  // The room's spawn slots are Turku's (server/spawns.json, around the office): another city starts a fresh join at
  // its own ground-checked start instead.
  if (!pose && start) return { x: start.x, y: start.y, z: start.z, yaw: start.yaw };
  const at = pose ?? you.spawn;
  return { x: at.E, y: pose?.H ?? 0, z: -at.N, yaw: at.yaw };
}

/** All positions a menu choice can use, including the car restored before the character. */
export function startPoints(spawn: V3, car: V3, games: readonly (SaveGame | null)[], multiplayer: V3 | null): V3[] {
  const points = [spawn, car, ...multiplayer ? [multiplayer] : []];
  for (const game of games) {
    if (game?.player) points.push(enToEngine(game.player.E, game.player.N, game.player.H));
    if (game?.lastVehicle) points.push(enToEngine(game.lastVehicle.E, game.lastVehicle.N, game.lastVehicle.H));
    if (game?.vehicle) points.push(enToEngine(game.vehicle.E, game.vehicle.N, game.vehicle.H));
  }
  return [...new Map(points.map(p => [`${p.x},${p.z}`, p])).values()];
}
