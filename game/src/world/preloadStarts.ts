import type * as THREE from 'three/webgpu';
import type { PlayWorld } from '../play';
import type { SaveGame } from '../core/save';
import type { SaveGameController } from '../gameplay/saveGame';
import { startPoints } from '../gameplay/startPoints';
import { preloadWorld } from './preload';

/** Retain nearby ground for all menu choices; the ordinary distance/velocity ring resumes on entry. */
export async function preloadStarts(world: PlayWorld, feet: { x: number; z: number }, games: (SaveGame | null)[],
  multiplayer: (PlayWorld['spawn']) | null, saves: SaveGameController, audio: Promise<void>[], actors: readonly THREE.Object3D[] = []) {
  const { streamer, coarse } = world;
  const starts = startPoints(world.spawn, world.car, games, multiplayer);
  streamer.retainedStarts = starts;
  if (coarse) coarse.retainedStarts = starts;
  // The spawn's view wedge loads full detail with the start ground (CoarseTiles.preload).
  const yaw = world.spawn.yaw;
  const releaseDrafts = coarse?.preload(starts, streamer.radius, false,
    { x: world.spawn.x, z: world.spawn.z, dx: -Math.sin(yaw), dz: -Math.cos(yaw) });
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
