import type * as THREE from 'three/webgpu';
import type { PlayWorld } from '../play';
import type { SaveGame } from '../core/save';
import type { SaveGameController } from '../gameplay/saveGame';
import { startPoints } from '../gameplay/startPoints';
import { preloadWorld } from './preload';

/** Streams the ground of the menu's starts and the placed spawn's view wedge (CoarseTiles.preload); returns the
 *  release. The session calls it as soon as its start is placed, before its models load, and preloadStarts again with
 *  the multiplayer start: placeStart streams the requested heading's wedge, the placed start faces along its street,
 *  and that wedge's tiles were wanted only after the models and street graph had loaded, then held the menu
 *  1-2 s on the M1 Air (loadspeed 2026-11-09). */
export function retainStarts(world: PlayWorld, games: readonly (SaveGame | null)[], multiplayer: PlayWorld['spawn'] | null) {
  const { streamer, coarse } = world;
  const starts = startPoints(world.spawn, world.car, games, multiplayer);
  streamer.retainedStarts = starts;
  if (coarse) coarse.retainedStarts = starts;
  const yaw = world.spawn.yaw;
  return coarse?.preload(starts, streamer.radius, false,
    { x: world.spawn.x, z: world.spawn.z, dx: -Math.sin(yaw), dz: -Math.cos(yaw) });
}

/** Retain nearby ground for all menu choices; the ordinary distance/velocity ring resumes on entry. */
export async function preloadStarts(world: PlayWorld, feet: { x: number; z: number }, games: (SaveGame | null)[],
  multiplayer: (PlayWorld['spawn']) | null, saves: SaveGameController, audio: Promise<void>[], actors: readonly THREE.Object3D[] = []) {
  const { streamer, coarse } = world;
  const releaseDrafts = retainStarts(world, games, multiplayer);
  await Promise.all([preloadWorld(world, feet, async () => {
    for (const game of [null, ...games.filter(g => g !== null)]) await saves.prepare(game);
    if (multiplayer) await saves.prepare(null, multiplayer);
  }, actors), ...audio]);
  let retained = true;
  return () => {
    if (!retained) return;
    retained = false;
    streamer.retainedStarts = [];
    streamer.radius = world.params.collisionRadius;
    if (coarse) coarse.retainedStarts = [];
    releaseDrafts?.();
  };
}
