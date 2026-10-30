import type * as THREE from 'three/webgpu';
import type { CoarseFragment, CoarseHost, Entry } from './coarseContracts';
import { distToL0, startupDraftTiles } from './coarseRegion';

interface Scenery { model?: THREE.Object3D; hash?: string; complete?: boolean; busy?: boolean; next: number; error?: string }

/** Optional photo scenery: authored visuals without distant collision, roads or parked-car simulation. */
export class ScreenshotScenery {
  radius = 0;
  private entries = new Map<string, Scenery>();
  private active = 0;
  private clock = 0;
  private wanted: string[] = [];
  constructor(private host: CoarseHost, private nearby: Map<string, Entry>,
    private request: (url: string) => Promise<Response>, private load: (url: string) => Promise<THREE.Object3D>,
    private style: (model: THREE.Object3D) => void, private dispose: (model: THREE.Object3D, id: string) => void,
    private root: string, private page: string) {}

  setRadius(radius: number) {
    this.radius = Number.isFinite(radius) ? Math.max(0, Math.min(5000, radius)) : 0;
    if (!this.radius) {
      for (const [id, e] of this.entries) if (e.model) this.dispose(e.model, id);
      this.entries.clear(); this.wanted = [];
    }
  }

  get progress() {
    return { loaded: this.wanted.filter(id => this.entries.get(id)?.model).length, total: this.wanted.length,
      pending: this.wanted.filter(id => !this.entries.get(id)?.complete).length,
      errors: this.wanted.filter(id => this.entries.get(id)?.error).length };
  }

  /** A bounded advancing demand window; completed scenery no longer occupies the service's 128-id lease. */
  demand() { return this.wanted.filter(id => {
    const e = this.entries.get(id);
    return !e?.complete && (!e?.error || this.clock >= e.next);
  }).slice(0, 64); }

  update(x: number, z: number, now: number, paused = false) {
    this.clock = now;
    if (!this.radius) return;
    this.wanted = startupDraftTiles([{ x, z }], this.radius)
      .filter(id => !this.nearby.get(id)?.model)
      .sort((a, b) => distToL0(a, x, z) - distToL0(b, x, z));
    const keep = new Set(this.wanted);
    for (const [id, e] of this.entries) if (!keep.has(id)) {
      if (e.model) this.dispose(e.model, id);
      this.entries.delete(id);
    }
    if (paused) return;
    const demand = new Set(this.demand());
    // Oldest poll first avoids starving the rest of a large view when nearby requests fail.
    const due = this.wanted.filter(id => demand.has(id) || this.entries.get(id)?.complete)
      .sort((a, b) => (this.entries.get(a)?.next ?? 0) - (this.entries.get(b)?.next ?? 0));
    for (const id of due) {
      if (this.active >= 2) break;
      let e = this.entries.get(id);
      if (!e) { e = { next: 0 }; this.entries.set(id, e); }
      if (e.busy || now < e.next) continue;
      e.busy = true; this.active++;
      void this.poll(id, e, distToL0(id, x, z));
    }
  }

  private async poll(id: string, e: Scenery, distance: number) {
    let model: THREE.Object3D | undefined;
    try {
      const res = await this.request(`${this.root}${id}/manifest.json?d=${Math.round(distance)}`);
      if (res.status === 202 || res.status === 429) { e.complete = false; return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const fragment = await res.json() as CoarseFragment;
      const visual = fragment.visual?.[id];
      if (!fragment.coarse || fragment.tile !== id || !visual) throw new Error('Missing scenery');
      e.complete = fragment.pass_kind === 'full' && !fragment.stale;
      e.error = undefined;
      if (e.model && e.hash === visual.sha256) return;
      model = await this.load(new URL(visual.url, new URL(`${this.root}${id}/`, this.page)).href);
      await this.host.style?.(model); this.style(model); await this.host.styled?.(model);
      if (this.entries.get(id) !== e || this.nearby.get(id)?.model) return;
      if (e.model) this.dispose(e.model, id);
      this.host.scene.add(model); this.host.trackModel?.(model, id);
      e.model = model; e.hash = visual.sha256; model = undefined;
    } catch (error) { e.error = String(error); e.complete = false; }
    finally {
      if (model) this.dispose(model, id);
      e.next = this.clock + (e.complete ? 30_000 : e.error ? 10_000 : 2000);
      e.busy = false; this.active--;
    }
  }
}
