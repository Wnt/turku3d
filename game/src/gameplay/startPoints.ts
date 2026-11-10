import { enToEngine, type V3 } from '../core/origin';
import type { SaveGame } from '../core/save';
import room from '../../../server/spawns.json';

/** Start pairs (own car, player beside it) the players of a room take by room slot (world/startGround.ts
 *  startPairs): one per spawn slot of the room, so players present together never share one. */
export const ROOM_START_CARS = room.points.length;
/** The index of the room's spawn slot `spawn` (server/spawns.json order; the room gives a joining player the lowest
 *  slot no present player holds, in every city), or 0. */
export function roomSlot(spawn?: { E: number; N: number } | null) {
  const i = spawn ? room.points.findIndex(p => Math.abs(p.E - spawn.E) < 0.01 && Math.abs(p.N - spawn.N) < 0.01) : -1;
  return Math.max(0, i);
}

/** Multiplayer uses local E/N; saves use EPSG:3877. Keep that conversion at this boundary. */
export function multiplayerStart(you: { spawn: { E: number; N: number; yaw: number } },
  self?: { pose: { E: number; N: number; H: number; yaw: number } | null },
  seat?: { pose: { E: number; N: number; H: number; yaw: number } },
  start?: { x: number; y: number; z: number; yaw: number }) {
  const pose = seat?.pose ?? self?.pose;
  // A fresh join (`fresh`) starts at the city's ground-checked start; the placement then takes this player's start
  // pair by its room slot (SaveGame startPair). The slot's own position is only the fallback without a start.
  if (!pose && start) return { x: start.x, y: start.y, z: start.z, yaw: start.yaw, fresh: true };
  const at = pose ?? you.spawn;
  return { x: at.E, y: pose?.H ?? 0, z: -at.N, yaw: at.yaw, ...pose ? {} : { fresh: true } };
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
