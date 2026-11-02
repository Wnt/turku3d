import { enToEngine, type V3 } from '../core/origin';
import type { SaveGame } from '../core/save';

/** Multiplayer uses local E/N; saves use EPSG:3877. Keep that conversion at this boundary. */
export function multiplayerStart(you: { spawn: { E: number; N: number; yaw: number } },
  self?: { pose: { E: number; N: number; H: number; yaw: number } | null },
  seat?: { pose: { E: number; N: number; H: number; yaw: number } }) {
  const pose = seat?.pose ?? self?.pose;
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
