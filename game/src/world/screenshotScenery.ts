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
import { LOD_FADE_BIAS, LOD_FADE_SECONDS, fadeTier, lodFades } from '../render/lodFade';
import { streamTrace } from '../telemetry/streamTrace';

interface Scenery { model?: THREE.Object3D; hash?: string; treesReady?: boolean; complete?: boolean; busy?: boolean; next: number; seen: number; error?: string }
class TreePlacementsPending extends Error {}

/** Full tiles nearer than this (m, 3D) cannot leave the window soon (it is kept to 325 m) nor yield to their cell by
 *  height, so their cells are released, not kept on standby: standby costs a decoded cell for each tile it covers. */
export const STANDBY_MARGIN = 150;

/** Bias tiers a cell of quadtree `level` can fade on: 250 m cells hand over and refine within ~1.8 km, 1 km nodes
 *  change at 0.7-4.5 km, 4 km nodes beyond (render/lodFade.ts LOD_FADE_BIAS). */
export const dissolveTiers = (level: number) => level === 0 ? [0, 1] : level === 2 ? [1, 2] : [2];

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
  /** True once scenery fully draws over the square (its own cell or a coarser node, landed), or when nothing ever
   *  will: no footprint node covers it, or none that covers it can load. A leaving full tile draws until then. */
  mayRelease(id: string) {
    if (!this.radius || this.drawnOver(id)) return true;
    return !this.coverPending(id);
  }

  /** Scenery that draws opaque over L0 square `id`: its own cell or a coarser city node, visible and not dissolving. */
  private drawnOver(id: string) {
    const own = this.entries.get(id)?.model;
    if (own?.visible && !own.userData.lodFading) return true;
    for (const [node, e] of this.entries) if (node !== id && e.model?.visible && !e.model.userData.lodFading
      && !this.sources.get(node)?.regional && sceneryContains(node, id)) return true;
    return false;
  }

  /** Whether a cover for square `id` is on its way: a footprint node over it that is loaded, or loadable. */
  private coverPending(id: string) {
    const own = !!this.entries.get(id)?.model;
    return this.wanted.some(node => (node === id || sceneryContains(node, id)) && !this.sources.get(node)?.regional
      && (own || !!this.entries.get(node)?.model || (this.sources.has(node) && !this.entries.get(node)?.error)));
  }
  releaseDetail(id: string) { this.held.delete(id); }
  private coarser: string[] = [];
  private coarserAt = -1;
  /** Trace coverage of one L0 square: C its own cell drawn, c dissolving, U a coarser node drawn over it. */
  cellState(id: string): 'C' | 'c' | 'U' | undefined {
    const model = this.entries.get(id)?.model;
    if (model?.visible) return model.userData.lodFading ? 'c' : 'C';
    if (this.coarserAt !== this.clock) {
      this.coarserAt = this.clock;
      this.coarser = [];
      for (const [other, e] of this.entries)
        if (e.model?.visible && sceneryBounds(other).level > 0 && !this.sources.get(other)?.regional) this.coarser.push(other);
    }
    return this.coarser.some(node => sceneryContains(node, id)) ? 'U' : undefined;
  }

  /** A resident full tile may yield to its cell only once that cell is loaded (standby or drawn). */
  canTakeOver(id: string) { return this.radius > 0 && !!this.entries.get(id)?.model && this.sources.has(id); }

  /** A detailed tile took over this cell: a drawn cell dissolves away on top of it instead of vanishing.
   *  It leaves its merged batch so that its own meshes carry the dissolve; the next cut hides it. */
  private handOver(model: THREE.Object3D) {
    if (!model.visible || (model.userData.lodFading && model.userData.lodFading !== 'in')) return;
    const id = [...this.entries].find(([, e]) => e.model === model)?.[0];
    this.fade(model, 'out', id);
  }

  /** Dissolve a scenery model in over what already draws there, or out over what replaces it. A reversal
   *  continues from the current coverage. Fading models draw their own meshes, outside merged batches. */
  /** Returns false when no compiled twin tier fits: the model then swaps at once (no in-frame shader build). */
  private fade(model: THREE.Object3D, direction: 'in' | 'out', id?: string): boolean {
    const from = model.userData.lodFading ? lodFades.coverage(model) : direction === 'in' ? 0 : 1;
    const view = this.view ?? this.footprintView;
    const distance = id && view ? Math.hypot(sceneryDistance(id, view.x, view.z), Math.max(0, view.y - 30)) : 0;
    // The ideal bias tier, else the nearest one this model's preparation compiled.
    const ideal = fadeTier(distance);
    const tier = model.userData.lodFading ? ideal
      : [ideal, ideal + 1, ideal - 1, ideal + 2, ideal - 2].find(t => t >= 0 && t < LOD_FADE_BIAS.length && lodFades.canStart(model, t));
    if (tier === undefined) {
      streamTrace.event('band.swap', id, { dir: direction, reason: 'twin not compiled' });
      model.userData.lodFading = undefined;
      model.visible = direction === 'in';
      this.batches.invalidate(model);
      return false;
    }
    model.userData.lodFading = direction;
    model.visible = true;
    this.batches.invalidate(model);
    streamTrace.event('band.fade', id, { dir: direction, from, tier, held: !!id && this.held.has(id) });
    lodFades.start(model, from, direction === 'in' ? 1 : 0, () => {
      streamTrace.event('band.fadeEnd', id, { dir: direction });
      model.userData.lodFading = undefined;
      model.visible = direction === 'in';
      this.batches.invalidate(model);
      this.cutState.clear(); // the landed model's cut visibility is decided again on the next update
    }, this.clock, LOD_FADE_SECONDS, tier);
    return true;
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
    if (e.model) streamTrace.event('cell.release', id, { level: sceneryBounds(id).level });
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

  /** Standby cells (hidden under a resident full tile) prepare no trees: the HD tile draws its own, and the cell's
   *  trees follow once it is wanted again. */
  private needsTrees(e: Scenery, distance: number, id?: string) {
    return !!this.host.prepareVegetation && !e.treesReady && distance < (this.host.vegetationDistance ?? 0)
      && !(id && this.standby.has(id));
  }

  private async prepareTrees(id: string, e: Scenery, model: THREE.Object3D, distance: number, source: ScenerySource) {
    if (!this.needsTrees(e,distance,id)) {
      // A standby cell within tree range still never exposes skyline cones up close once it is shown.
      if (this.standby.has(id) && distance < (this.host.vegetationDistance ?? 0)) {
        for (const child of model.children) if (child.userData.layer === 'vegetation') child.visible = false;
        this.batches.invalidate(model);
      }
      return;
    }
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

  /** The camera the scenery last followed (null without a far radius): the tile streamer installs full detail in it. */
  get cameraView(): Readonly<SceneryView> | null { return this.view; }

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
    this.fillWanted();
    this.refreshStandby();
  }

  /** The footprint minus unavailable and detailed squares, in place: this runs every frame. */
  private fillWanted() {
    const wanted = this.wanted;
    wanted.length = 0;
    for (const id of this.footprint) if (!this.unavailable.has(id) && !this.detailed(id)) wanted.push(id);
  }

  private refreshStandby() {
    this.standby.clear();
    const view = this.view;
    // 3D, like the band distances: a high flier keeps the cells under it on standby for the height switch.
    for (const id of this.footprint) if (sceneryBounds(id).level === 0 && !this.unavailable.has(id) && this.detailed(id)
      && (!view || Math.hypot(sceneryDistance(id, view.x, view.z), Math.max(0, view.y - 30)) >= STANDBY_MARGIN)) this.standby.add(id);
  }

  private readMissingSources() {
    if (this.cataloguePending || this.clock < this.catalogueRetry) return;
    // Refresh both positive and negative metadata: source coverage can arrive without a camera reset.
    const refresh = this.clock >= this.catalogueExpires;
    // Per frame: no array unless a footprint square still lacks source metadata.
    if (!refresh && !this.footprint.some(id => !this.sources.has(id) && !this.unavailable.has(id))) return;
    const missing = refresh ? this.footprint : this.footprint.filter(id => !this.sources.has(id) && !this.unavailable.has(id));
    if (missing.length) void this.catalogue(this.selection, missing, refresh);
  }

  private async catalogue(selection: number, tiles: string[], refresh = false) {
    this.cataloguePending = true;
    const traced = streamTrace.begin('cell.catalogue', undefined, { tiles: tiles.length, refresh });
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
      traced({ returned: returned.size, missing: tiles.length - returned.size, changed });
      if (refresh || changed) this.catalogueExpires = this.clock + 30_000;
      this.catalogueError = false;
      // A turn during the request changes the wanted sector, not the validity of cached source metadata.
      this.refreshWanted();
    } catch (error) {
      traced({ error: String(error) });
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
      this.fillWanted();
      this.refreshStandby();
      this.readMissingSources();
    }
    if (now >= this.nextCutExpiry || this.cutState.changed(this.wanted, this.entries, this.nearby, this.sources))
      this.commitCut(now, previousUpdate);
    this.batches.sync(this.batchModels);
    const concurrency = this.view ? 4 : 2;
    if (paused || this.active >= concurrency) return;
    this.entries.forEach((e, id) => { if (e.model && e.complete && e.next === Infinity
      && this.sources.get(id)?.trees_url && this.needsTrees(e,sceneryDistance(id,x,z),id) && !e.busy) e.next = 0; });
    const demand = this.view ? undefined : new Set(this.demand());
    // Live views keep skyline priority when a preparation retry becomes due.
    // Untouched entries must not outrank that retry just because their next time is zero.
    // The legacy full-detail view still rotates polling fairly across source generation.
    const start = (id: string) => {
      if (this.detailed(id) && !this.standby.has(id)) return;
      let e = this.entries.get(id);
      if (!e) { e = { next: 0, seen: now }; this.entries.set(id, e); }
      if (e.busy || now < e.next) return;
      e.busy = true; this.active++;
      void this.poll(id, e, sceneryDistance(id, x, z), this.sources.get(id));
    };
    if (this.view) {
      // Live views keep the skyline order of `wanted`; walked in place every frame, without a filtered copy.
      for (const id of this.wanted) {
        if (this.active >= concurrency) return;
        if (this.sources.has(id) && !this.detailed(id)) start(id);
      }
    } else {
      const due = this.wanted.filter(id => !this.detailed(id) && (demand!.has(id) || this.entries.get(id)?.complete))
        .sort((a, b) => (this.entries.get(a)?.next ?? 0) - (this.entries.get(b)?.next ?? 0));
      for (const id of due) { if (this.active >= concurrency) return; start(id); }
    }
    // Visible work first; standby cells fill the remaining slots.
    for (const id of this.standby) {
      if (this.active >= concurrency) return;
      if (this.sources.has(id) && !this.entries.get(id)?.model) start(id);
    }
  }

  private commitCut(now: number, previousUpdate: number) {
    if (!streamTrace.on) { this.commitCutNow(now, previousUpdate); return; }
    const started = performance.now(), stats = this.commitCutNow(now, previousUpdate), ms = performance.now() - started;
    streamTrace.slice('scenery', ms);
    streamTrace.event('cut.commit', undefined, { ...stats, ms });
  }

  private commitCutNow(now: number, previousUpdate: number) {
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
      if (!this.fade(model, 'in', id)) continue;
      for (const other of under) this.outgoing.set(other, Math.max(this.outgoing.get(other) ?? 0, until));
    }
    for (const [id, end] of this.outgoing) if (end <= now || !this.entries.get(id)?.model) this.outgoing.delete(id);
    // A cell under a resident full tile stays loaded (hidden) while the coarser footprint node over its square does
    // not draw yet: when the tile leaves, the cell covers the square (as a fallback) until that node lands.
    const underTile = (id: string, e: Scenery) => !!e.model && this.detailed(id) && sceneryBounds(id).level === 0
      && this.wanted.some(node => node !== id && sceneryContains(node, id) && !this.sources.get(node)?.regional
        && !(this.entries.get(node)?.model?.visible && !this.entries.get(node)!.model!.userData.lodFading));
    for (const [id, e] of this.entries) {
      // Stable cuts no longer touch seen every frame. Preserve the last frame
      // that still owned this tile, including long suspended-tab time gaps.
      if (this.committed.has(id)) e.seen = previousUpdate;
      if (keep.has(id) || fallback.has(id) || this.standby.has(id) || this.outgoing.has(id) || e.model?.userData.lodFading
        || underTile(id, e)) e.seen = now;
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
    let shown = 0, hidden = 0;
    for (const [id, e] of this.entries) if (e.model && !e.model.userData.lodFading) {
      const visible = (target.get(id) ?? false) || this.outgoing.has(id);
      if (visible !== e.model.visible) { if (visible) shown++; else hidden++; }
      e.model.visible = visible;
    }
    const stats = streamTrace.on ? { wanted: this.wanted.length, entries: this.entries.size, shown, hidden, outgoing: this.outgoing.size,
      held: this.held.size, standby: this.standby.size, pending: this.wanted.filter(id => !this.entries.get(id)?.model).length } : undefined;
    this.committed = new Set([...keep, ...fallback]);
    this.updateCoverage();
    this.batchModels = [];
    for (const [id, entry] of this.entries) if (entry.model) this.batchModels.push([id, entry.model]);
    this.cutState.capture(this.wanted, this.entries, this.nearby, this.sources);
    return stats;
  }

  private async poll(id: string, e: Scenery, distance: number, source?: ScenerySource) {
    let model: THREE.Object3D | undefined;
    const rank = streamTrace.on ? this.wanted.indexOf(id) : -1;
    const manifest = streamTrace.begin('cell.manifest', id, { level: sceneryBounds(id).level, rank, standby: this.standby.has(id), active: this.active });
    try {
      const res = await this.request(source ? `${this.root}scenery/${source.key}.json`
        : `${this.root}${id}/manifest.json?d=${Math.round(distance)}`);
      if (res.status === 202 || res.status === 429) { manifest({ status: res.status }); e.complete = false; return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const fragment = await res.json() as CoarseFragment;
      manifest({ status: 200 });
      if (source && this.sources.get(id)?.key !== source.key) return;
      const visual = fragment.visual?.[id];
      if (!fragment.coarse || fragment.tile !== id || !visual) throw new Error('Missing scenery');
      e.complete = fragment.pass_kind === 'full' && !fragment.stale;
      e.error = undefined;
      if (e.model && e.hash === visual.sha256) {
        if (source) await this.prepareTrees(id,e,e.model,distance,source);
        return;
      }
      const glb = streamTrace.begin('cell.glb', id, { bytes: visual.bytes, tris: visual.tris });
      model = await this.load(new URL(visual.url, new URL(`${this.root}${id}/`, this.page)).href);
      glb();
      if (source) {
        model.userData.scenery = true;
        const loaded = model;
        streamTrace.time('scenery', () => styleScenery(loaded, this.shared, this.coverage.texture));
        e.treesReady = false;
        const trees = streamTrace.begin('cell.trees', id);
        await this.prepareTrees(id,e,model,distance,source);
        trees({ ready: e.treesReady });
        const prepared = streamTrace.begin('cell.prepare', id);
        // Its preparation also compiles the dissolve twins of the bias tiers this level's fades use.
        model.userData.dissolveTiers = dissolveTiers(sceneryBounds(id).level);
        await this.host.prepareGraphics?.(model);
        prepared();
      } else { await this.host.style?.(model); this.style(model); await this.host.styled?.(model); }
      if (this.entries.get(id) !== e || (this.detailed(id) && !this.standby.has(id))
        || (source && this.sources.get(id)?.key !== source.key)) return;
      if (e.model) this.retireModel(e.model, id);
      if (this.view) model.visible = false; // Visibility is committed with the cut on the next update.
      this.host.scene.add(model); this.host.trackModel?.(model, id);
      e.model = model; e.hash = visual.sha256; model = undefined;
      streamTrace.event('cell.install', id, { level: sceneryBounds(id).level, tris: visual.tris, standby: this.standby.has(id), detailed: this.detailed(id) });
      this.nearbyChanged(id);
    } catch (error) {
      e.error = error instanceof TreePlacementsPending ? undefined : String(error); e.complete = false;
      if (e.error) streamTrace.event('cell.error', id, { error: e.error });
    }
    finally {
      if (model) this.dispose(model, id);
      if (source && this.sources.get(id)?.key !== source.key) { e.complete = false; e.next = this.clock; }
      else e.next = source && e.complete ? Infinity : this.clock + (e.complete ? 30_000 : e.error ? 10_000 : 2000);
      e.busy = false; this.active--;
    }
  }
}
