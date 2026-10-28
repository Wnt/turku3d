// A standard TilesRenderer load-region plugin: traversal, priority queues, parsing and LRU ownership stay in
// 3DTilesRendererJS. Play mode uses distance alone; rotating the render camera cannot change the request set.
import type { Tile, TilesRenderer } from '3d-tiles-renderer';
import { LRUCache } from '3d-tiles-renderer/core';
import { distToVisualTile, ringParents, VISUAL_RING_RADIUS } from './residency';

const HORIZON = 2000;
const HEADROOM = 256 * 1024 ** 2;

export class PlayerLoadRegion {
  readonly name = 'player-load-region';
  private renderer!: TilesRenderer;
  private root: Tile | null = null;
  private parents = new Map<string, string[]>();
  private selected = new Set<string>();
  private path = new Set<Tile>();
  private refine = new Set<Tile>();
  private required = new Set<Tile>();
  private loaded = new Set<Tile>();
  private x = NaN;
  private z = NaN;
  private plannedRadius = NaN;
  radius = VISUAL_RING_RADIUS;
  private retained: readonly { x: number; z: number }[] = [];

  retain(points: readonly { x: number; z: number }[]) { this.retained = points; this.plan(); }

  init(renderer: TilesRenderer) {
    this.renderer = renderer;
    // A release being swapped in must not share another release's capacity or evict its live models.
    const cache = new LRUCache();
    cache.unloadPriorityCallback = renderer.lruCache.unloadPriorityCallback;
    renderer.lruCache = cache;
    renderer.lruCache.autoMarkUnused = false;
    renderer.autoDisableRendererCulling = false;
    renderer.addEventListener('load-root-tileset', ({ tileset }) => {
      this.root = tileset.root;
      const index = (tile: Tile) => {
        const children = (tile.children ?? []).map(c => c.extras?.id as string).filter(id => id?.startsWith('t0_'));
        if (children.length && tile.extras?.id) this.parents.set(tile.extras.id, children);
        tile.children?.forEach(index);
      };
      index(this.root);
      this.plan();
    });
    renderer.addEventListener('load-model', ({ tile }) => this.loaded.add(tile));
    renderer.addEventListener('dispose-model', ({ tile }) => this.loaded.delete(tile));
  }

  move(x: number, z: number) {
    if (x !== this.x || z !== this.z || this.radius !== this.plannedRadius) {
      this.x = x; this.z = z;
      this.plannedRadius = this.radius;
      this.plan();
    }
    // The library's byte cap is an admission cap, including used tiles. A fixed 430 MB cap can silently
    // stop refinement forever. Reserve headroom above the protected ring; unused content still evicts normally.
    const cache = this.renderer.lruCache;
    const usedBytes = [...this.path].reduce((n, tile) => n + (cache.getMemoryUsage(tile) || 0), 0);
    cache.minBytesSize = usedBytes + HEADROOM / 4;
    cache.maxBytesSize = usedBytes + HEADROOM;
    cache.minSize = this.path.size + 16;
    cache.maxSize = this.path.size + 64;
  }

  private plan() {
    if (!this.root || !Number.isFinite(this.x)) return;
    const points = [{ x: this.x, z: this.z }, ...this.retained];
    this.selected = new Set(points.flatMap(p => [...ringParents(this.parents, p.x, p.z, this.selected, this.radius)]));
    this.path.clear(); this.refine.clear(); this.required.clear();
    const visit = (tile: Tile, sibling = false): boolean => {
      const id = tile.extras?.id as string | undefined;
      const children = tile.children ?? [];
      const d = id ? Math.min(...points.map(p => distToVisualTile(id, p.x, p.z))) : 0;
      if (!sibling && d > (id?.startsWith('t0_') ? this.radius : HORIZON)) return false;
      this.path.add(tile);
      const split = !tile.content || !id || !id.startsWith('t1_') || this.selected.has(id);
      if (children.length && split) {
        let any = false;
        for (const child of children) any = visit(child, this.selected.has(id ?? '')) || any;
        if (any) { this.refine.add(tile); return true; }
      }
      if (tile.content) this.required.add(tile);
      return true;
    };
    visit(this.root);
  }

  calculateTileViewError(tile: Tile, target: { inView: boolean; error: number; distance: number }) {
    target.inView = this.path.has(tile);
    target.error = this.refine.has(tile) ? this.renderer.errorTarget + 1 : 0;
    const id = tile.extras?.id as string | undefined;
    target.distance = id ? distToVisualTile(id, this.x, this.z) : 0;
    return true; // mask camera-frustum results, including tiles outside the distance region
  }

  get progress() {
    return { loaded: [...this.required].filter(tile => this.loaded.has(tile)).length, total: this.required.size };
  }

  get settled() {
    const { loaded, total } = this.progress;
    return total > 0 && loaded === total && this.renderer.loadProgress >= 1;
  }
}
