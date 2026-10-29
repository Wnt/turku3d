import type { PlayWorld } from '../play';
import type { SaveGame } from '../core/save';
import type { SaveGameController } from '../gameplay/saveGame';
import { startPoints, warmupStarts } from '../gameplay/startPoints';
import { preloadWorld } from './preload';

/** Retain nearby ground for all menu choices; the ordinary distance/velocity ring resumes on entry. */
export async function preloadStarts(world: PlayWorld, feet: { x: number; z: number }, games: (SaveGame | null)[],
  multiplayer: (PlayWorld['spawn']) | null, saves: SaveGameController, audio: Promise<void>[]) {
  const { streamer, coarse } = world;
  const starts = startPoints(world.spawn, world.car, games, multiplayer);
  streamer.retainedStarts = starts;
  if (coarse) coarse.retainedStarts = starts;
  const releaseDrafts = coarse?.preload(starts, streamer.radius);
  await Promise.all([preloadWorld(world, feet, async () => {
    const views = [];
    for (const game of [null, ...games.filter(g => g !== null)]) views.push(await saves.prepare(game));
    if (multiplayer) views.push(await saves.prepare(null, multiplayer));
    return warmupStarts(views);
  }), ...audio]);
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
