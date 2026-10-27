// Gameplay entry for main.ts: the street router and the delivery address pool handed to the mission system, and
// the minimap.
import type { Manifest } from '../world/manifest';
import { loadAddressPool } from './content/addresses';
import { Minimap } from './minimap';
import { loadRoadGraphJson, mergeRoadGraphs, type RoadGraphJson } from './roadGraph';
import { StreetRouter } from './router';
import { PlayableArea } from './recovery';

export { compassDeg } from './minimap';

type GameplayManifest = Pick<Manifest, 'roads' | 'addresses' | 'visual' | 'baseUrl' | 'collision'>;

/** The street map the router and the minimap share: the published graph (read again when a release changes
 *  `manifest.roads`) merged with the draft tiles' graphs. `update` is serialised; the latest call wins. */
class StreetMap {
  private published: RoadGraphJson | null;
  private sha: string | undefined;
  private chain = Promise.resolve();
  updates = 0;

  constructor(private readonly manifest: GameplayManifest, published: RoadGraphJson | null,
    private readonly router: StreetRouter, private readonly minimap: Minimap) {
    this.published = published;
    this.sha = manifest.roads?.sha256;
  }

  /** Re-merges with these draft graphs (CoarseTiles.roadGraphs). */
  update(drafts: readonly RoadGraphJson[]): Promise<void> {
    this.chain = this.chain.then(async () => {
      if (this.manifest.roads?.sha256 !== this.sha) {
        this.sha = this.manifest.roads?.sha256;
        this.published = await loadRoadGraphJson(this.manifest);
      }
      const graph = mergeRoadGraphs([...this.published ? [this.published] : [], ...drafts]);
      this.router.setGraph(graph);
      this.minimap.setGraph(graph);
      this.updates++;
    }).catch(e => console.warn(`[map] ${(e as Error).message}`));
    return this.chain;
  }
}

/** Loads `manifest.roads` and `manifest.addresses`; an empty graph (no streets, every route null) and an empty pool
 *  (the scripted deliveries repeat) when the build has none. The playable area is the union of the manifest's
 *  collision tiles. */
export async function createGameplay(manifest: GameplayManifest, parent: HTMLElement) {
  const [published, addresses] = await Promise.all([loadRoadGraphJson(manifest), loadAddressPool(manifest)]);
  const graph = mergeRoadGraphs(published ? [published] : []);
  const router = new StreetRouter(graph);
  const area = new PlayableArea(manifest.collision);
  const minimap = new Minimap(parent, graph, router);
  minimap.setArea(area);
  return { router, minimap, area, addresses, streets: new StreetMap(manifest, published, router, minimap) };
}
