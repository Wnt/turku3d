// Gameplay entry for main.ts: the street router and the delivery address pool handed to the mission system, and
// the minimap.
import { isLocalHost } from '../core/localOnly';
import type { CoarseTiles } from '../world/coarseTiles';
import type { Manifest } from '../world/manifest';
import { draftsUrl } from '../net/config';
import { parseAddressPool, type AddressJson, loadAddressPool } from './content/addresses';
import { withLandmarks } from './content/landmarks';
import { Minimap } from './minimap';
import { loadRoadGraphJson, type RoadGraphJson } from './roadGraph';
import { mergeRoadGraphsAsync } from './roadGraphAsync';
import { readStreamingJson } from '../world/streamingJson';
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
  private revision = 0;
  private appliedPublished: RoadGraphJson | null;
  private appliedDrafts: readonly RoadGraphJson[] = [];
  updates = 0;

  constructor(private readonly manifest: GameplayManifest, published: RoadGraphJson | null,
    private readonly router: StreetRouter, private readonly minimap: Minimap, private readonly cityWide = false) {
    this.published = published;
    this.appliedPublished = published;
    this.sha = manifest.roads?.sha256;
  }

  /** Keep resident tile fragments connected across streamed replacements. */
  connectDrafts(coarse: CoarseTiles | null) {
    if (!coarse) return;
    coarse.onMapChange = () => void this.update(coarse.roadGraphs());
    if (coarse.roadGraphs().length) coarse.onMapChange();
  }

  /** Re-merges with these draft graphs (CoarseTiles.roadGraphs). */
  update(drafts: readonly RoadGraphJson[]): Promise<void> {
    // A city graph is complete before tiles load; resident clipped fragments must never replace its topology.
    if (this.cityWide) return Promise.resolve();
    const revision = ++this.revision;
    this.chain = this.chain.then(async () => {
      if (revision !== this.revision) return;
      if (this.manifest.roads?.sha256 !== this.sha) {
        const sha = this.manifest.roads?.sha256;
        this.published = await loadRoadGraphJson(this.manifest);
        this.sha = sha;
      }
      if (revision !== this.revision) return;
      if (this.published === this.appliedPublished && drafts.length === this.appliedDrafts.length
        && drafts.every((part, index) => part === this.appliedDrafts[index])) return;
      const graph = await mergeRoadGraphsAsync([...this.published ? [this.published] : [], ...drafts]);
      if (revision !== this.revision) return;
      this.router.setGraph(graph);
      this.minimap.setGraph(graph);
      this.appliedPublished = this.published;
      this.appliedDrafts = [...drafts];
      this.updates++;
    }).catch(e => console.warn(`[map] ${(e as Error).message}`));
    return this.chain;
  }
}

/** Loads `manifest.roads` and `manifest.addresses`; an empty graph (no streets, every route null) and an empty pool
 *  (the scripted deliveries repeat) when the build has none. A pool without landmarks gets the curated landmarks'
 *  fallback entries, so long jobs never depend on resident tiles. The playable area is the union of the manifest's
 *  collision tiles. */
export async function createGameplay(manifest: GameplayManifest, parent: HTMLElement) {
  const endpoint = draftsUrl();
  const navigation = endpoint ? await fetch(new URL('navigation.json', new URL(endpoint, location.href))).then(async res => {
    if (res.status === 404 && isLocalHost(location.hostname)) {
      // Synthetic dev service or the shared local tile farm: resident street tiles only.
      console.warn('[gameplay] no /coarse/navigation.json: resident street graph, landmark fallbacks for long jobs');
      return null;
    }
    if (!res.ok) throw new Error(`navigation: HTTP ${res.status}`);
    return readStreamingJson<{ roads: RoadGraphJson; addresses: AddressJson }>(res);
  }) : null;
  const [published, pool] = navigation
    ? [navigation.roads, parseAddressPool(navigation.addresses, () => true)]
    : await Promise.all([loadRoadGraphJson(manifest), loadAddressPool(manifest)]);
  const graph = await mergeRoadGraphsAsync(published ? [published] : []);
  const router = new StreetRouter(graph);
  const area = new PlayableArea(manifest.collision);
  const minimap = new Minimap(parent, graph, router);
  minimap.setArea(area);
  return { router, minimap, area, addresses: withLandmarks(pool), streets: new StreetMap(manifest, published, router, minimap, navigation !== null) };
}
