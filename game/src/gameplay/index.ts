// Gameplay entry for main.ts: the street router handed to the mission system and the minimap.
import type { Manifest } from '../world/manifest';
import { Minimap } from './minimap';
import { loadRoadGraph } from './roadGraph';
import { StreetRouter } from './router';
import { PlayableArea } from './recovery';

export { compassDeg } from './minimap';

/** Loads `manifest.roads`; an empty graph (no streets, every route null) when the build has none. The playable
 *  area is the union of the manifest's collision tiles. */
export async function createGameplay(manifest: Pick<Manifest, 'roads' | 'baseUrl' | 'collision'>, parent: HTMLElement) {
  const graph = await loadRoadGraph(manifest);
  const router = new StreetRouter(graph);
  const area = new PlayableArea(manifest.collision);
  const minimap = new Minimap(parent, graph, router);
  minimap.setArea(area);
  return { router, minimap, area };
}
