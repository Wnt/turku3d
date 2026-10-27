// Keep the exact loaded objects across a release: renderer caches and material styling belong to those objects.
import { Group, type Object3D } from 'three';
import type { Tile, TilesRenderer } from '3d-tiles-renderer';
import type { ManifestEntry } from './manifest';

export function unchangedVisualTiles(old: Record<string, ManifestEntry>, fresh: Record<string, ManifestEntry>): Set<string> {
  return new Set(Object.keys(fresh).filter(id => !!old[id] && old[id].sha256 === fresh[id].sha256));
}

// engineData is part of 3DTilesRendererJS's runtime tile contract but absent from its Tile declaration.
interface ModelTile extends Tile {
  engineData: { scene: Object3D | null; geometry: unknown; materials: unknown; textures: unknown; metadata: unknown };
}
const data = (tile: Tile) => (tile as ModelTile).engineData;

/** Borrow models while the next view settles; ownership moves only at the visible swap. */
export class TileReuse {
  private donors = new Map<string, Tile>();
  private borrowed = new Map<Tile, Tile>();
  private urls = new Set<string>();
  loaded = 0;
  private source: TilesRenderer | null;

  constructor(source: TilesRenderer, private target: TilesRenderer, unchanged: ReadonlySet<string>) {
    this.source = source;
    source.forEachLoadedModel((_scene, tile) => {
      const id = tile.extras?.id as string | undefined;
      if (id && unchanged.has(id)) this.donors.set(id, tile);
    });
    // Protect the donor models until transfer, including those just outside the current view.
    source.lruCache.autoMarkUnused = false;
    for (const tile of this.donors.values()) source.lruCache.markUsed(tile);
  }

  readonly name = 'release-model-reuse';
  readonly priority = -100;

  preprocessURL(url: string, tile: Tile | null) {
    if (tile && this.donors.has(tile.extras?.id)) this.urls.add(url);
    return url;
  }

  fetchData(url: string) {
    return this.urls.has(url) ? Promise.resolve(new ArrayBuffer(0)) : null;
  }

  parseTile(_buffer: ArrayBuffer, tile: Tile) {
    const donor = this.donors.get(tile.extras?.id);
    if (!donor) return null;
    // An empty scene lets traversal track visibility without reparenting the live model.
    const placeholder = new Group();
    placeholder.userData.releasePlaceholder = true;
    Object.assign(data(tile), { scene: placeholder, geometry: [], materials: [], textures: [] });
    this.borrowed.set(tile, donor);
    this.loaded++;
    return Promise.resolve();
  }

  disposeTile(tile: Tile) {
    if (this.borrowed.delete(tile)) this.loaded--;
  }

  dispose() {
    if (this.source) this.source.lruCache.autoMarkUnused = true;
    this.source = null;
    this.donors.clear();
    this.borrowed.clear();
    this.urls.clear();
  }

  transfer() {
    if (!this.source) return;
    for (const [tile, donor] of this.borrowed) {
      const from = data(donor), to = data(tile);
      to.scene?.removeFromParent();
      Object.assign(to, { scene: from.scene, geometry: from.geometry, materials: from.materials,
        textures: from.textures, metadata: from.metadata });
      // Old disposal must neither release these resources nor emit dispose-model for a retained model.
      this.target.lruCache.setMemoryUsage(tile, this.source.lruCache.getMemoryUsage(donor));
      from.scene = null;
      to.scene?.removeFromParent();
      if (to.scene) {
        if (this.target.visibleTiles.has(tile)) this.target.group.add(to.scene);
        else if (this.target.activeTiles.has(tile)) to.scene.parent = this.target.group;
      }
    }
    this.dispose();
  }
}
