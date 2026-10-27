// Gameplay entry for main.ts: the street router and the delivery address pool handed to the mission system, and
// the minimap.
import type { Manifest } from '../world/manifest';
import { loadAddressPool } from './content/addresses';
import { Minimap } from './minimap';
import { loadRoadGraph } from './roadGraph';
import { StreetRouter } from './router';
import { PlayableArea } from './recovery';

export { compassDeg } from './minimap';

/** Loads `manifest.roads` and `manifest.addresses`; an empty graph (no streets, every route null) and an empty pool
 *  (the scripted deliveries repeat) when the build has none. The playable area is the union of the manifest's
 *  collision tiles. */
export async function createGameplay(
  manifest: Pick<Manifest, 'roads' | 'addresses' | 'visual' | 'baseUrl' | 'collision'>, parent: HTMLElement) {
  const [graph, addresses] = await Promise.all([loadRoadGraph(manifest), loadAddressPool(manifest)]);
  const router = new StreetRouter(graph);
  const area = new PlayableArea(manifest.collision);
  const minimap = new Minimap(parent, graph, router);
  minimap.setArea(area);
  return { router, minimap, area, addresses };
}
