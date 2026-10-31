import type * as THREE from 'three/webgpu';
import type { CoarseFragment, CoarseHost, Entry } from './coarseContracts';
import { distToL0, startupDraftTiles } from './coarseRegion';
import { sceneryTiles, skylinePriority, type SceneryView, type ScenerySource } from './sceneryView';

interface Scenery { model?: THREE.Object3D; hash?: string; complete?: boolean; busy?: boolean; next: number; error?: string }

/** Optional photo scenery: authored visuals without distant collision, roads or parked-car simulation. */
export class ScreenshotScenery {
  radius = 0;
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
  private missing = 0;
  get heading() { return this.view ? (Math.atan2(this.view.dx, -this.view.dz) * 180 / Math.PI + 360) % 360 : null; }
  constructor(private host: CoarseHost, private nearby: Map<string, Entry>,
    private request: (url: string) => Promise<Response>, private load: (url: string) => Promise<THREE.Object3D>,
    private style: (model: THREE.Object3D) => void, private dispose: (model: THREE.Object3D, id: string) => void,
    private root: string, private page: string) {}

  setRadius(radius: number, view?: SceneryView) {
    this.radius = Number.isFinite(radius) ? Math.max(0, Math.min(8000, radius)) : 0;
    const selection = ++this.selection;
    for (const [id, e] of this.entries) if (e.model) this.dispose(e.model, id);
    this.entries.clear(); this.sources.clear(); this.wanted = []; this.footprint = [];
    this.catalogueError = false; this.cataloguePending = false; this.missing = 0;
    this.view = this.radius > 1000 && view ? { ...view } : null;
    if (this.view) {
      this.wanted = sceneryTiles(this.radius, this.view);
      void this.catalogue(selection);
    }
  }

  private async catalogue(selection: number) {
    this.cataloguePending = true;
    try {
      const batches: string[][] = [];
      for (let i = 0; i < this.wanted.length; i += 256) batches.push(this.wanted.slice(i, i + 256));
      const results = await Promise.all(batches.map(async tiles => {
        const response = await this.request(`${this.root}scenery?tiles=${tiles.join(',')}`);
        if (!response.ok) throw new Error('Scenery catalogue unavailable');
        return response.json() as Promise<{ tiles: ScenerySource[]; missing: string[] }>;
      }));
      if (selection !== this.selection) return;
      for (const result of results) {
        for (const source of result.tiles) this.sources.set(source.tile, source);
        this.missing += result.missing.length;
      }
      this.wanted = this.wanted.filter(id => this.sources.has(id)).sort((a, b) =>
        skylinePriority(this.sources.get(b)!, this.view!) - skylinePriority(this.sources.get(a)!, this.view!));
      this.footprint = this.wanted;
    } catch { if (selection === this.selection) this.catalogueError = true; }
    finally { if (selection === this.selection) this.cataloguePending = false; }
  }

  get progress() {
    return { loaded: this.wanted.filter(id => this.entries.get(id)?.model).length, total: this.wanted.length,
      pending: this.wanted.filter(id => !this.entries.get(id)?.complete).length,
      errors: this.wanted.filter(id => this.entries.get(id)?.error).length + Number(this.catalogueError),
      missing: this.missing, cataloguePending: this.cataloguePending };
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
      .filter(id => !this.nearby.get(id)?.model)
      .sort((a, b) => distToL0(a, x, z) - distToL0(b, x, z));
    if (this.view && !this.cataloguePending) this.wanted = this.footprint.filter(id => !this.nearby.get(id)?.model);
    const keep = new Set(this.wanted);
    for (const [id, e] of this.entries) if (!keep.has(id)) {
      if (e.model) this.dispose(e.model, id);
      this.entries.delete(id);
    }
    if (paused || this.cataloguePending || this.catalogueError) return;
    const demand = new Set(this.demand());
    // Oldest poll first avoids starving the rest of a large view when nearby requests fail.
    const due = this.wanted.filter(id => (!this.nearby.get(id)?.model || this.nearby.get(id)?.preview)
      && (this.view || demand.has(id) || this.entries.get(id)?.complete))
      .sort((a, b) => (this.entries.get(a)?.next ?? 0) - (this.entries.get(b)?.next ?? 0));
    for (const id of due) {
      if (this.active >= (this.view ? 8 : 2)) break;
      let e = this.entries.get(id);
      if (!e) { e = { next: 0 }; this.entries.set(id, e); }
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
      const visual = fragment.visual?.[id];
      if (!fragment.coarse || fragment.tile !== id || !visual) throw new Error('Missing scenery');
      e.complete = fragment.pass_kind === 'full' && !fragment.stale;
      e.error = undefined;
      if (e.model && e.hash === visual.sha256) return;
      model = await this.load(new URL(visual.url, new URL(`${this.root}${id}/`, this.page)).href);
      if (source) {
        model.userData.scenery = true;
        model.traverse(object => {
          const mesh = object as THREE.Mesh;
          if (mesh.isMesh) { mesh.castShadow = false; mesh.receiveShadow = false; }
        });
      } else { await this.host.style?.(model); this.style(model); await this.host.styled?.(model); }
      if (this.entries.get(id) !== e || this.nearby.get(id)?.model) return;
      if (e.model) this.dispose(e.model, id);
      this.host.scene.add(model); this.host.trackModel?.(model, id);
      e.model = model; e.hash = visual.sha256; model = undefined;
    } catch (error) { e.error = String(error); e.complete = false; }
    finally {
      if (model) this.dispose(model, id);
      e.next = source && e.complete ? Infinity : this.clock + (e.complete ? 30_000 : e.error ? 10_000 : 2000);
      e.busy = false; this.active--;
    }
  }
}
