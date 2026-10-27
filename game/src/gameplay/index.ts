// Gameplay entry for main.ts: the street router handed to the mission system and the minimap.
import type { Manifest } from '../world/manifest';
import { Minimap } from './minimap';
import { loadRoadGraph } from './roadGraph';
import { StreetRouter } from './router';

export { compassDeg } from './minimap';

/** Loads `manifest.roads`; an empty graph (no streets, every route null) when the build has none. */
export async function createGameplay(manifest: Pick<Manifest, 'roads' | 'baseUrl'>, parent: HTMLElement) {
  const graph = await loadRoadGraph(manifest);
  const router = new StreetRouter(graph);
  return { router, minimap: new Minimap(parent, graph, router) };
}
