import * as THREE from 'three/webgpu';
import { SceneryCutState } from './sceneryCutState';
import { SceneryCoverage } from '../render/sceneryCoverage';
import { SceneryBatches } from '../render/sceneryBatches';
import { styleScenery } from '../render/sceneryMaterial';
import type { SharedMaterials } from '../render/sharedMaterials';
import type { CoarseFragment, CoarseHost, Entry } from './coarseContracts';
import { distToL0, startupDraftTiles } from './coarseRegion';
import { sceneryTiles, regionalTiles, groundHalfAngle, sceneryBounds, sceneryDistance, sceneryContains, skylinePriority, type SceneryView, type ScenerySource } from './sceneryView';
import type { TreePlacements } from './treePlacements';
import { LOD_FADE_SECONDS, lodFades } from '../render/lodFade';

interface Scenery { model?: THREE.Object3D; hash?: string; treesReady?: boolean; complete?: boolean; busy?: boolean; next: number; seen: number; error?: string }
class TreePlacementsPending extends Error {}

/** Lightweight distant city: authored visuals without collision, shadows or simulation. */
export class ScreenshotScenery {
  radius = 0;
  private readonly coverage = new SceneryCoverage();
  private readonly cutState = new SceneryCutState();
  private committed = new Set<string>();
  private nextCutExpiry = Infinity;
  private batchModels: [string, THREE.Object3D][] = [];
  private readonly batches: SceneryBatches;
  private readonly direction = new THREE.Vector3();

  /** Follow every rendered camera, including play and fixed QA poses. */
  follow(camera: THREE.PerspectiveCamera) {
    camera.getWorldDirection(this.direction);
    const vertical = Math.abs(this.direction.y), horizontal = Math.hypot(this.direction.x, this.direction.z);
    const halfAngle = groundHalfAngle(vertical, horizontal, camera.fov, camera.aspect);
    this.direction.y = 0;
    this.direction.normalize();
    const { x, y, z } = camera.position;
    this.setView({ x, y, z, dx: this.direction.x, dz: this.direction.z,
      halfAngle, focalPixels: (this.host.renderer.domElement?.height ?? 800)
        * camera.projectionMatrix.elements[5] / 2 }, performance.now());
  }
  private detailed(id: string) {
    const entry = this.nearby.get(id);
    return !!entry?.model && !entry.preview && entry.cachedAt === undefined && !entry.distant;
  }

  /** Apply ownership synchronously when a near visual or prepared scenery model becomes resident. */
  nearbyChanged(id: string) {
    const model = this.entries.get(id)?.model;
    if (model && this.detailed(id)) this.handOver(model);
    this.updateCoverage();
  }

  /** Full visuals that no longer own their square but still draw while the cell dissolves in over them. */
  private held = new Set<string>();
  /** Scenery models kept drawn (opaque) under incoming dissolves until this time. */
  private outgoing = new Map<string, number>();

  /** A full tile is leaving its square (window exit, return cache or a distant camera): keep drawing it until
   *  `mayRelease` says the scenery cell covers the square. */
  holdDetail(id: string) { this.held.add(id); }
  /** False while the cell is wanted here and not yet fully drawn; a square no camera footprint needs never waits. */
  mayRelease(id: string) {
    const model = this.entries.get(id)?.model;
    if (!this.radius || !this.wanted.includes(id)) return true;
    return !!model?.visible && !model.userData.lodFading;
  }
  releaseDetail(id: string) { this.held.delete(id); }
  /** A resident full tile may yield to its cell only once that cell is loaded (standby or drawn). */
  canTakeOver(id: string) { return this.radius > 0 && !!this.entries.get(id)?.model && this.sources.has(id); }

  /** A detailed tile took over this cell: a drawn cell dissolves away on top of it instead of vanishing.
   *  It leaves its merged batch so that its own meshes carry the dissolve; the next cut hides it. */
  private handOver(model: THREE.Object3D) {
    if (!model.visible || (model.userData.lodFading && model.userData.lodFading !== 'in')) return;
    this.fade(model, 'out');
  }

  /** Dissolve a scenery model in over what already draws there, or out over what replaces it. A reversal
   *  continues from the current coverage. Fading models draw their own meshes, outside merged batches. */
  private fade(model: THREE.Object3D, direction: 'in' | 'out') {
    const from = model.userData.lodFading ? lodFades.coverage(model) : direction === 'in' ? 0 : 1;
    model.userData.lodFading = direction;
    model.visible = true;
    this.batches.invalidate(model);
    lodFades.start(model, from, direction === 'in' ? 1 : 0, () => {
      model.userData.lodFading = undefined;
      model.visible = direction === 'in';
      this.batches.invalidate(model);
      this.cutState.clear(); // the landed model's cut visibility is decided again on the next update
    }, this.clock);
  }

  private updateCoverage() {
    const city = [...this.entries].filter(([id, e]) => e.model?.visible && !this.sources.get(id)?.regional);
    // Preserve the collision preview until richer city geometry is actually prepared.
    for (const [id, entry] of this.nearby) if (entry.preview && entry.model)
      entry.model.visible = !city.some(([parent]) => sceneryContains(parent, id));
    this.coverage.update([
      ...[...this.nearby].filter(([, e]) => e.model?.visible).map(([id]) => id),
      ...city.map(([id]) => id),
    ]);
  }

  private release(id: string, e: Scenery) {
    const nearby = this.nearby.get(id);
    if (nearby?.preview && nearby.model) nearby.model.visible = true;
    if (e.model) this.retireModel(e.model,id);
  }

  private retireModel(model: THREE.Object3D, id: string) {
    lodFades.cancel(model);
    model.userData.lodFading = undefined;
    model.removeFromParent();
    this.batches.retireModel(model, () => this.dispose(model, id));
  }

  private needsTrees(e: Scenery, distance: number) {
    return !!this.host.prepareVegetation && !e.treesReady && distance < (this.host.vegetationDistance ?? 0);
  }

  private async prepareTrees(id: string, e: Scenery, model: THREE.Object3D, distance: number, source: ScenerySource) {
    if (!this.needsTrees(e,distance)) return;
    // A camera approaching a cached far tile must never expose its skyline cones up close.
    for (const child of model.children) if (child.userData.layer === 'vegetation') child.visible = false;
    this.batches.invalidate(model);
    if (!source.trees_url) return;
    const response = await this.request(new URL(source.trees_url,new URL(this.root,this.page)).href);
    if (response.status === 202) throw new TreePlacementsPending();
    if (!response.ok) throw new Error(`Tree placements: HTTP ${response.status}`);
    const data = await response.json() as TreePlacements;
    if (data.version !== 1 || !Array.isArray(data.nodes)) throw new Error('Tree placements pending');
    await this.host.prepareVegetation!(model,data);
    this.batches.invalidate(model);
    if (this.entries.get(id) === e) e.treesReady = true;
  }

  private entries = new Map<string, Scenery>();
  private active = 0;
  private clock = 0;
  private wanted: string[] = [];
  /** L0 cells of the live footprint under resident full tiles: loaded but hidden, so a leaving tile's cell is
   *  already prepared when it takes the square back (no hole while it would otherwise fetch). */
  private standby = new Set<string>();
  private view: SceneryView | null = null;
  private footprint: string[] = [];
  private sources = new Map<string, ScenerySource>();
  private selection = 0;
  private cataloguePending = false;
  private catalogueError = false;
  private unavailable = new Set<string>();
  private footprintView: SceneryView | null = null;
  private nextView = 0;
  private catalogueRetry = 0;
  private catalogueExpires = 0;
  private generation?: string;
  get heading() { return this.view ? (Math.atan2(this.view.dx, -this.view.dz) * 180 / Math.PI + 360) % 360 : null; }
  constructor(private host: CoarseHost, private nearby: Map<string, Entry>,
    private request: (url: string) => Promise<Response>, private load: (url: string) => Promise<THREE.Object3D>,
    private style: (model: THREE.Object3D) => void, private dispose: (model: THREE.Object3D, id: string) => void,
    private root: string, private page: string, private shared?: SharedMaterials) {
    this.batches = new SceneryBatches(host.scene, host.retire, host.prepareGraphics);
  }

  setRadius(radius: number, view?: SceneryView) {
    for (const e of this.entries.values()) if (e.model?.userData.lodFading) { lodFades.cancel(e.model); e.model.userData.lodFading = undefined; }
    this.outgoing.clear();
    this.radius = Number.isFinite(radius) ? Math.max(0, Math.min(15000, radius)) : 0;
    ++this.selection;
    this.batches.clear(); this.cutState.clear(); this.committed.clear(); this.nextCutExpiry = Infinity; this.batchModels = [];
    for (const [id, e] of this.entries) this.release(id,e);
    for (const entry of this.nearby.values()) if (entry.preview && entry.model) entry.model.visible = true;
    this.coverage.update([]);
    this.entries.clear(); this.sources.clear(); this.wanted = []; this.footprint = []; this.standby.clear();
    this.catalogueError = false; this.cataloguePending = false; this.unavailable.clear();
    this.footprintView = null; this.nextView = 0; this.catalogueRetry = 0;
    this.catalogueExpires = 0; this.generation = undefined;
    this.view = this.radius > 1000 && view ? { ...view } : null;
    if (this.view) this.setView(this.view, this.clock);
  }

  /** Follow the live camera, with a small refresh interval to avoid catalogue churn while turning. */
  setView(view: SceneryView, now = this.clock) {
    if (this.radius <= 1000) return;
    this.view = { ...view };
    const previous = this.footprintView;
    if (previous && (now < this.nextView || (Math.hypot(view.x - previous.x, view.z - previous.z) < 25
      && Math.abs(view.y - previous.y) < 10 && Math.abs(view.halfAngle - previous.halfAngle) < .01
      && Math.abs((view.focalPixels ?? 600) - (previous.focalPixels ?? 600)) < 30
      && view.dx * previous.dx + view.dz * previous.dz > Math.cos(Math.PI / 180)))) return;
    this.nextView = now + 150;
    this.footprintView = { ...view };
    this.footprint = [...new Set([...regionalTiles(view), ...sceneryTiles(this.radius, view, this.footprint)])];
    this.refreshWanted();
    this.readMissingSources();
  }

  private refreshWanted() {
    const priorities = new Map(this.footprint.map(id => {
      const source = this.sources.get(id);
      return [id, source?.regional ? 100 - sceneryDistance(id, this.view!.x, this.view!.z) / 1000
        : source ? skylinePriority(source, this.view!) : -Infinity];
    }));
    this.footprint.sort((a, b) => priorities.get(b)! - priorities.get(a)!);
    this.wanted = this.footprint.filter(id => !this.unavailable.has(id) && !this.detailed(id));
    this.refreshStandby();
  }

  private refreshStandby() {
    this.standby.clear();
    for (const id of this.footprint) if (sceneryBounds(id).level === 0 && !this.unavailable.has(id) && this.detailed(id)) this.standby.add(id);
  }

  private readMissingSources() {
    if (this.cataloguePending || this.clock < this.catalogueRetry) return;
    // Refresh both positive and negative metadata: source coverage can arrive without a camera reset.
    const refresh = this.clock >= this.catalogueExpires;
    const missing = refresh ? this.footprint : this.footprint.filter(id => !this.sources.has(id) && !this.unavailable.has(id));
    if (missing.length) void this.catalogue(this.selection, missing, refresh);
  }

  private async catalogue(selection: number, tiles: string[], refresh = false) {
    this.cataloguePending = true;
    try {
      const batches: string[][] = [];
      for (let i = 0; i < tiles.length; i += 256) batches.push(tiles.slice(i, i + 256));
      const results = await Promise.all(batches.map(async batch => {
        const response = await this.request(`${this.root}scenery?tiles=${batch.join(',')}`);
        if (!response.ok) throw new Error('Scenery catalogue unavailable');
        return response.json() as Promise<{ tiles: ScenerySource[]; missing: string[]; generation?: string; recipe?: string }>;
      }));
      if (selection !== this.selection) return;
      const generations = new Set(results.map(result => result.generation ?? result.recipe).filter(value => value !== undefined));
      if (generations.size > 1) throw new Error('Scenery generation changed during catalogue loading');
      const generation = [...generations][0];
      const changed = generation !== undefined && generation !== this.generation;
      if (changed) {
        this.generation = generation;
        this.sources.clear(); this.unavailable.clear();
      }
      const returned = new Set<string>();
      for (const result of results) for (const source of result.tiles) {
        returned.add(source.tile);
        const previous = this.sources.get(source.tile), entry = this.entries.get(source.tile);
        if (entry && previous?.key !== source.key) { entry.complete = false; entry.next = 0; entry.error = undefined; }
        this.sources.set(source.tile, source); this.unavailable.delete(source.tile);
      }
      for (const id of tiles) if (!returned.has(id)) { this.sources.delete(id); this.unavailable.add(id); }
      if (refresh || changed) this.catalogueExpires = this.clock + 30_000;
      this.catalogueError = false;
      // A turn during the request changes the wanted sector, not the validity of cached source metadata.
      this.refreshWanted();
    } catch {
      if (selection === this.selection) { this.catalogueError = true; this.catalogueRetry = this.clock + 2000; }
    } finally { if (selection === this.selection) this.cataloguePending = false; }
  }

  get progress() {
    const coveredSquareKm = this.footprint.reduce((area, id) => {
      const source = this.sources.get(id);
      return area + (source && !source.regional ? (source.coverage ?? 1) * sceneryBounds(id).size ** 2 / 1e6 : 0);
    }, 0);
    const partial = this.wanted.filter(id => (this.sources.get(id)?.coverage ?? 1) < 1).length;
    const loaded = this.wanted.filter(id => this.entries.get(id)?.model).length;
    const pending = this.wanted.filter(id => !this.entries.get(id)?.complete || this.entries.get(id)?.busy).length;
    const errors = this.wanted.filter(id => this.entries.get(id)?.error).length + Number(this.catalogueError);
    const missing = this.footprint.filter(id => this.unavailable.has(id) && !this.detailed(id)).length;
    const batching = this.batches.pending;
    const committed = this.wanted.every(id=>this.entries.get(id)?.model?.visible);
    return { ready: committed && !batching && !partial && !pending && !errors && !missing && !this.cataloguePending && loaded === this.wanted.length,
      coveredSquareKm, partial, loaded, total: this.wanted.length, pending, errors, missing, batching, cataloguePending: this.cataloguePending };
  }

  snapshot() {
    return { progress: this.progress, tiles: this.wanted.map(id => ({ id, source: this.sources.get(id),
      hash: this.entries.get(id)?.hash, complete: this.entries.get(id)?.complete })) };
  }

  /** A bounded advancing demand window; completed scenery no longer occupies the service's 128-id lease. */
  demand() { if (this.view) return [];
    return this.wanted.filter(id => {
    const e = this.entries.get(id);
    return !e?.complete && (!e?.error || this.clock >= e.next);
  }).slice(0, 64); }

  update(x: number, z: number, now: number, paused = false) {
    const previousUpdate = this.clock;
    this.clock = now;
    if (!this.radius) return;
    if (!this.view) this.wanted = startupDraftTiles([{ x, z }], this.radius)
      .filter(id => !this.detailed(id))
      .sort((a, b) => distToL0(a, x, z) - distToL0(b, x, z));
    if (this.view) {
      this.wanted = this.footprint.filter(id => !this.unavailable.has(id) && !this.detailed(id));
      this.refreshStandby();
      this.readMissingSources();
    }
    if (now >= this.nextCutExpiry || this.cutState.changed(this.wanted, this.entries, this.nearby, this.sources))
      this.commitCut(now, previousUpdate);
    this.batches.sync(this.batchModels);
    const concurrency = this.view ? 4 : 2;
    if (paused || this.active >= concurrency) return;
    for (const [id,e] of this.entries) if (e.model && e.complete && e.next === Infinity
      && this.sources.get(id)?.trees_url && this.needsTrees(e,sceneryDistance(id,x,z)) && !e.busy) e.next = 0;
    const demand = new Set(this.demand());
    // Live views keep skyline priority when a preparation retry becomes due.
    // Untouched entries must not outrank that retry just because their next time is zero.
    // The legacy full-detail view still rotates polling fairly across source generation.
    const due = this.wanted.filter(id => !this.detailed(id)
      && (this.view ? this.sources.has(id) : demand.has(id) || this.entries.get(id)?.complete))
      .sort((a, b) => this.view ? 0 : (this.entries.get(a)?.next ?? 0) - (this.entries.get(b)?.next ?? 0));
    // Visible work first; standby cells fill the remaining slots.
    for (const id of this.standby) if (this.sources.has(id) && !this.entries.get(id)?.model) due.push(id);
    for (const id of due) {
      if (this.active >= concurrency) break;
      if (this.detailed(id) && !this.standby.has(id)) continue;
      let e = this.entries.get(id);
      if (!e) { e = { next: 0, seen: now }; this.entries.set(id, e); }
      if (e.busy || now < e.next) continue;
      e.busy = true; this.active++;
      void this.poll(id, e, sceneryDistance(id, x, z), this.sources.get(id));
    }
  }

  private commitCut(now: number, previousUpdate: number) {
    const keep = new Set(this.wanted);
    this.nextCutExpiry = Infinity;
    const outside: [string, Scenery][] = [];
    // Keep the old cut during refinement AND coarsening until its replacement is ready.
    const fallback = new Set<string>();
    // A cell under a resident full tile (standby or dissolving) never stands in as a fallback: HD already draws there.
    for (const [id, e] of this.entries) if (e.model && !this.sources.get(id)?.regional && !keep.has(id) && !this.detailed(id)
      && this.wanted.some(next => (sceneryContains(id, next) || sceneryContains(next, id))
        && !this.sources.get(next)?.regional && !this.entries.get(next)?.model)) fallback.add(id);
    // Target visibility of the new cut. A newly shown model dissolves in over whatever drew there (the coarser or
    // finer models it replaces, or a held full tile), which stays opaque underneath until the dissolve lands.
    const parents = [...fallback];
    const target = new Map<string, boolean>();
    for (const [id, e] of this.entries) if (e.model && !e.model.userData.lodFading)
      target.set(id, !parents.some(parent => sceneryContains(parent, id) && parent !== id) && (keep.has(id) || fallback.has(id)));
    const hiding = [...target].filter(([id, show]) => !show && this.entries.get(id)!.model!.visible).map(([id]) => id);
    const until = now + LOD_FADE_SECONDS * 1000;
    for (const [id, show] of target) {
      const model = this.entries.get(id)!.model!;
      if (!show || model.visible) continue;
      const under = hiding.filter(other => sceneryContains(other, id) || sceneryContains(id, other));
      if (!under.length && !this.held.has(id)) continue;
      this.fade(model, 'in');
      for (const other of under) this.outgoing.set(other, Math.max(this.outgoing.get(other) ?? 0, until));
    }
    for (const [id, end] of this.outgoing) if (end <= now || !this.entries.get(id)?.model) this.outgoing.delete(id);
    for (const [id, e] of this.entries) {
      // Stable cuts no longer touch seen every frame. Preserve the last frame
      // that still owned this tile, including long suspended-tab time gaps.
      if (this.committed.has(id)) e.seen = previousUpdate;
      if (keep.has(id) || fallback.has(id) || this.standby.has(id) || this.outgoing.has(id) || e.model?.userData.lodFading) e.seen = now;
      else if (this.view && !this.detailed(id) && now - e.seen < 5000) outside.push([id, e]);
      else { this.release(id,e); this.entries.delete(id); }
    }
    // A bounded five-second tail makes brief look-backs instant without retaining a whole 360° world.
    outside.sort((a, b) => b[1].seen - a[1].seen);
    for (const [id, e] of outside.slice(128)) {
      this.release(id,e);
      this.entries.delete(id);
    }
    for (const [, e] of outside.slice(0,128)) this.nextCutExpiry = Math.min(this.nextCutExpiry, e.seen + 5000);
    for (const end of this.outgoing.values()) this.nextCutExpiry = Math.min(this.nextCutExpiry, end);
    for (const [id, e] of this.entries) if (e.model && !e.model.userData.lodFading)
      e.model.visible = (target.get(id) ?? false) || this.outgoing.has(id);
    this.committed = new Set([...keep, ...fallback]);
    this.updateCoverage();
    this.batchModels = [];
    for (const [id, entry] of this.entries) if (entry.model) this.batchModels.push([id, entry.model]);
    this.cutState.capture(this.wanted, this.entries, this.nearby, this.sources);
  }

  private async poll(id: string, e: Scenery, distance: number, source?: ScenerySource) {
    let model: THREE.Object3D | undefined;
    try {
      const res = await this.request(source ? `${this.root}scenery/${source.key}.json`
        : `${this.root}${id}/manifest.json?d=${Math.round(distance)}`);
      if (res.status === 202 || res.status === 429) { e.complete = false; return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const fragment = await res.json() as CoarseFragment;
      if (source && this.sources.get(id)?.key !== source.key) return;
      const visual = fragment.visual?.[id];
      if (!fragment.coarse || fragment.tile !== id || !visual) throw new Error('Missing scenery');
      e.complete = fragment.pass_kind === 'full' && !fragment.stale;
      e.error = undefined;
      if (e.model && e.hash === visual.sha256) {
        if (source) await this.prepareTrees(id,e,e.model,distance,source);
        return;
      }
      model = await this.load(new URL(visual.url, new URL(`${this.root}${id}/`, this.page)).href);
      if (source) {
        model.userData.scenery = true;
        styleScenery(model, this.shared, this.coverage.texture);
        e.treesReady = false;
        await this.prepareTrees(id,e,model,distance,source);
        await this.host.prepareGraphics?.(model);
      } else { await this.host.style?.(model); this.style(model); await this.host.styled?.(model); }
      if (this.entries.get(id) !== e || (this.detailed(id) && !this.standby.has(id))
        || (source && this.sources.get(id)?.key !== source.key)) return;
      if (e.model) this.retireModel(e.model, id);
      if (this.view) model.visible = false; // Visibility is committed with the cut on the next update.
      this.host.scene.add(model); this.host.trackModel?.(model, id);
      e.model = model; e.hash = visual.sha256; model = undefined;
      this.nearbyChanged(id);
    } catch (error) { e.error = error instanceof TreePlacementsPending ? undefined : String(error); e.complete = false; }
    finally {
      if (model) this.dispose(model, id);
      if (source && this.sources.get(id)?.key !== source.key) { e.complete = false; e.next = this.clock; }
      else e.next = source && e.complete ? Infinity : this.clock + (e.complete ? 30_000 : e.error ? 10_000 : 2000);
      e.busy = false; this.active--;
    }
  }
}
