import type * as THREE from 'three/webgpu';
import type { CoarseHost } from './coarseTiles';

export interface ComparisonEntry {
  model?: THREE.Object3D; pass?: 'first' | 'full'; comparison?: THREE.Object3D;
  comparing?: boolean; comparisonMode?: string; comparisonNext?: number;
}

/** Separate developer visual residency; never publishes collision or touches streaming state. */
export class VisualPassComparison {
  constructor(private host: CoarseHost, private entries: Map<string, ComparisonEntry>,
    private request: (url: string) => Promise<Response>, private load: (url: string) => Promise<THREE.Object3D>,
    private style: (model: THREE.Object3D) => void, private dispose: (model: THREE.Object3D) => void,
    private root: string, private page: string) {}
  clock = 0;
  /** Developer display override; collision and normal streaming always keep their current owner. */
  pass: 'auto' | 'first' | 'full' = 'auto';
  status = '';

  setPass(pass: 'auto' | 'first' | 'full') {
    this.pass = pass;
    for (const e of this.entries.values()) {
      this.clear(e); e.comparisonNext = 0;
      if (e.model) e.model.visible = pass === 'auto' || e.pass === pass;
    }
    this.status = '';
  }

  clear(e: ComparisonEntry) {
    if (e.model) e.model.visible = true;
    if (e.comparison) {
      this.host.untrackModel?.(e.comparison, 'comparison');
      this.host.unstyle?.(e.comparison); e.comparison.removeFromParent(); this.dispose(e.comparison);
      e.comparison = undefined;
    }
    e.comparisonMode = undefined;
  }

  async compare(id: string, e: ComparisonEntry) {
    const pass = this.pass;
    if (pass === 'auto' || !e.model || e.comparing) return;
    e.model.visible = e.pass === pass;
    if (e.pass === pass) { this.clear(e); return; }
    if (e.comparisonMode === pass || this.clock < (e.comparisonNext ?? 0)) return;
    e.comparing = true; e.comparisonNext = this.clock + 5000;
    let model: THREE.Object3D | null = null;
    try {
      const res = await this.request(`${this.root}${id}/${pass === 'first' ? 'first' : 'manifest'}.json`);
      if (!res.ok || res.status === 202) throw new Error(`${id}: ${pass} pass pending`);
      const fragment = await res.json() as { pass_kind: string; visual: Record<string, { url: string }> };
      if (fragment.pass_kind !== pass || !fragment.visual?.[id]) throw new Error(`${id}: ${pass} pass pending`);
      model = await this.load(new URL(fragment.visual[id].url, new URL(`${this.root}${id}/`, this.page)).href);
      await this.host.style?.(model);
      this.style(model);
      await this.host.styled?.(model);
      if (this.entries.get(id) !== e || this.pass !== pass || !e.model) return;
      this.clear(e);
      e.comparison = model; e.comparisonMode = pass; e.model.visible = false;
      this.host.scene.add(model); this.host.trackModel?.(model, 'comparison'); model = null;
      this.status = '';
    } catch (error) { this.status = (error as Error).message; }
    finally {
      if (model) { this.host.unstyle?.(model); this.dispose(model); }
      e.comparing = false;
    }
  }

}
