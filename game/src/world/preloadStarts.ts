import type { PlayWorld } from '../play';
import type { SaveGame } from '../core/save';
import type { SaveGameController } from '../gameplay/saveGame';
import { startPoints, warmupStarts } from '../gameplay/startPoints';
import { VISUAL_RING_RADIUS } from './residency';
import { preloadWorld } from './preload';

/** Retain the union, rather than preloading candidates sequentially and evicting an earlier choice. */
export async function preloadStarts(world: PlayWorld, feet: { x: number; z: number }, games: (SaveGame | null)[],
  multiplayer: (PlayWorld['spawn']) | null, saves: SaveGameController, audio: Promise<void>[]) {
  const { tiles, streamer, coarse } = world;
  const starts = startPoints(world.spawn, world.car, games, multiplayer);
  tiles.setFocus(feet.x, feet.z, Math.max(VISUAL_RING_RADIUS, world.params.collisionRadius));
  tiles.retainStarts(starts);
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
    tiles.retainStarts([]);
    streamer.retainedStarts = [];
    if (coarse) coarse.retainedStarts = [];
    releaseDrafts?.();
  };
}
