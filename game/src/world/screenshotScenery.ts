import * as THREE from 'three/webgpu';
import { styleScenery } from '../render/sceneryMaterial';
import type { CoarseFragment, CoarseHost, Entry } from './coarseContracts';
import { distToL0, startupDraftTiles } from './coarseRegion';
import { sceneryTiles, skylinePriority, type SceneryView, type ScenerySource } from './sceneryView';

interface Scenery { model?: THREE.Object3D; hash?: string; complete?: boolean; busy?: boolean; next: number; seen: number; error?: string }

/** Lightweight distant city: authored visuals without collision, shadows or simulation. */
export class ScreenshotScenery {
  radius = 0;
  private readonly direction = new THREE.Vector3();

  /** Follow every rendered camera, including play and fixed QA poses. */
  follow(camera: THREE.PerspectiveCamera) {
    camera.getWorldDirection(this.direction);
    this.direction.y = 0;
    this.direction.normalize();
    const { x, y, z } = camera.position;
    this.setView({ x, y, z, dx: this.direction.x, dz: this.direction.z,
      halfAngle: Math.atan(Math.tan(camera.fov * Math.PI / 360) * camera.aspect) + .15 }, performance.now());
  }
  private detailed(id: string) {
    const entry = this.nearby.get(id);
    return !!entry?.model && !entry.preview;
  }

  private entries = new Map<string, Scenery>();
  private active = 0;
  private clock = 0;
  private wanted: string[] = [];
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
    private root: string, private page: string) {}

  setRadius(radius: number, view?: SceneryView) {
    this.radius = Number.isFinite(radius) ? Math.max(0, Math.min(8000, radius)) : 0;
    ++this.selection;
    for (const [id, e] of this.entries) if (e.model) this.dispose(e.model, id);
    this.entries.clear(); this.sources.clear(); this.wanted = []; this.footprint = [];
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
      && view.dx * previous.dx + view.dz * previous.dz > Math.cos(Math.PI / 180)))) return;
    this.nextView = now + 150;
    this.footprintView = { ...view };
    this.footprint = sceneryTiles(this.radius, view);
    this.refreshWanted();
    this.readMissingSources();
  }

  private refreshWanted() {
    const priorities = new Map(this.footprint.map(id => {
      const source = this.sources.get(id);
      return [id, source ? skylinePriority(source, this.view!) : -Infinity];
    }));
    this.footprint.sort((a, b) => priorities.get(b)! - priorities.get(a)!);
    this.wanted = this.footprint.filter(id => !this.unavailable.has(id) && !this.detailed(id));
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
        if (entry && previous?.key !== source.key) { entry.complete = false; entry.next = 0; }
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
    return { loaded: this.wanted.filter(id => this.entries.get(id)?.model).length, total: this.wanted.length,
      pending: this.wanted.filter(id => !this.entries.get(id)?.complete).length,
      errors: this.wanted.filter(id => this.entries.get(id)?.error).length + Number(this.catalogueError),
      missing: this.footprint.filter(id => this.unavailable.has(id)).length, cataloguePending: this.cataloguePending };
  }

  /** A bounded advancing demand window; completed scenery no longer occupies the service's 128-id lease. */
  demand() { if (this.view) return [];
    return this.wanted.filter(id => {
    const e = this.entries.get(id);
    return !e?.complete && (!e?.error || this.clock >= e.next);
  }).slice(0, 64); }

  update(x: number, z: number, now: number, paused = false) {
    this.clock = now;
    if (!this.radius) return;
    if (!this.view) this.wanted = startupDraftTiles([{ x, z }], this.radius)
      .filter(id => !this.detailed(id))
      .sort((a, b) => distToL0(a, x, z) - distToL0(b, x, z));
    if (this.view) {
      this.wanted = this.footprint.filter(id => !this.unavailable.has(id) && !this.detailed(id));
      this.readMissingSources();
    }
    const keep = new Set(this.wanted);
    const outside: [string, Scenery][] = [];
    for (const [id, e] of this.entries) {
      if (keep.has(id)) e.seen = now;
      else if (this.view && !this.detailed(id) && now - e.seen < 5000) outside.push([id, e]);
      else { if (e.model) this.dispose(e.model, id); this.entries.delete(id); }
    }
    // A bounded five-second tail makes brief look-backs instant without retaining a whole 360° world.
    outside.sort((a, b) => b[1].seen - a[1].seen);
    for (const [id, e] of outside.slice(128)) {
      if (e.model) this.dispose(e.model, id);
      this.entries.delete(id);
    }
    if (paused) return;
    const demand = new Set(this.demand());
    // Live views keep skyline priority when a preparation retry becomes due.
    // Untouched entries must not outrank that retry just because their next time is zero.
    // The legacy full-detail view still rotates polling fairly across source generation.
    const due = this.wanted.filter(id => !this.detailed(id)
      && (this.view ? this.sources.has(id) : demand.has(id) || this.entries.get(id)?.complete))
      .sort((a, b) => this.view ? 0 : (this.entries.get(a)?.next ?? 0) - (this.entries.get(b)?.next ?? 0));
    for (const id of due) {
      if (this.active >= (this.view ? 4 : 2)) break;
      let e = this.entries.get(id);
      if (!e) { e = { next: 0, seen: now }; this.entries.set(id, e); }
      if (e.busy || now < e.next) continue;
      e.busy = true; this.active++;
      void this.poll(id, e, distToL0(id, x, z), this.sources.get(id));
    }
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
      if (e.model && e.hash === visual.sha256) return;
      model = await this.load(new URL(visual.url, new URL(`${this.root}${id}/`, this.page)).href);
      if (source) {
        model.userData.scenery = true;
        styleScenery(model);
      } else { await this.host.style?.(model); this.style(model); await this.host.styled?.(model); }
      if (this.entries.get(id) !== e || this.detailed(id) || (source && this.sources.get(id)?.key !== source.key)) return;
      if (e.model) this.dispose(e.model, id);
      this.host.scene.add(model); this.host.trackModel?.(model, id);
      e.model = model; e.hash = visual.sha256; model = undefined;
    } catch (error) { e.error = String(error); e.complete = false; }
    finally {
      if (model) this.dispose(model, id);
      if (source && this.sources.get(id)?.key !== source.key) { e.complete = false; e.next = this.clock; }
      else e.next = source && e.complete ? Infinity : this.clock + (e.complete ? 30_000 : e.error ? 10_000 : 2000);
      e.busy = false; this.active--;
    }
  }
}
