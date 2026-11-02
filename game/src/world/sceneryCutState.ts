import type { Object3D } from 'three/webgpu';

interface Resident { model?: Object3D }
interface Nearby extends Resident { preview?: unknown; cachedAt?: number }
interface State extends Resident { visible?: boolean; regional?: boolean; preview?: boolean; cached?: boolean }

/** Detect ownership changes without allocating a per-frame scene signature.
 * Polling/busy timestamps do not change the visible quadtree cut. */
export class SceneryCutState {
  private wanted: string[] = [];
  private models = new Map<string, State>();
  private nearby = new Map<string, State>();
  private valid = false;

  clear() { this.valid = false; this.wanted = []; this.models.clear(); this.nearby.clear(); }

  changed(wanted: readonly string[], models: ReadonlyMap<string, Resident>,
    nearby: ReadonlyMap<string, Nearby>, sources: ReadonlyMap<string, {regional?: boolean}>) {
    if (!this.valid || wanted.length !== this.wanted.length || models.size !== this.models.size
      || nearby.size !== this.nearby.size) return true;
    for (let i=0;i<wanted.length;i++) if (wanted[i] !== this.wanted[i]) return true;
    for (const [id, entry] of models) {
      const old = this.models.get(id);
      if (!old || old.model !== entry.model || old.visible !== entry.model?.visible
        || old.regional !== !!sources.get(id)?.regional) return true;
    }
    for (const [id, entry] of nearby) {
      const old = this.nearby.get(id);
      if (!old || old.model !== entry.model || old.visible !== entry.model?.visible
        || old.preview !== !!entry.preview || old.cached !== (entry.cachedAt !== undefined)) return true;
    }
    return false;
  }

  capture(wanted: readonly string[], models: ReadonlyMap<string, Resident>,
    nearby: ReadonlyMap<string, Nearby>, sources: ReadonlyMap<string, {regional?: boolean}>) {
    this.wanted = [...wanted]; this.models.clear(); this.nearby.clear();
    for (const [id, entry] of models) this.models.set(id, {
      model: entry.model, visible: entry.model?.visible, regional: !!sources.get(id)?.regional,
    });
    for (const [id, entry] of nearby) this.nearby.set(id, {
      model: entry.model, visible: entry.model?.visible, preview: !!entry.preview, cached: entry.cachedAt !== undefined,
    });
    this.valid = true;
  }
}
