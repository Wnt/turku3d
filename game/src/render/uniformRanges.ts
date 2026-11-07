import type * as THREE from 'three/webgpu';

type Range = { start: number; count: number; cycle?: number };
interface RangeGroup {
  _updateRangeCache: Map<number, Range>;
  updateRanges: Range[];
  _rangeCycle: number;
}
interface Uniform { index: number; offset: number; itemSize: number }

/** r186 UniformsGroup remembers which uniforms already queued their update range this upload in a Set that
 * clearUpdateRanges empties after every upload (Bindings._update). Clearing a Set gives it a fresh hash table, so every
 * uniform group written in a frame (per draw and pass: object matrices, material values) allocated a new table that
 * lived until the next upload: the largest surviving allocation under _renderObjectDirect in city.lab play
 * (framecheck mac-heap-1: ~1 MB/s of scavenge survivors, each an old-to-new pointer). The cached range objects carry
 * the upload cycle they were queued in instead; the queued ranges and their order are unchanged. */
export function stampUniformRanges(proto: object) {
  const parent = Object.getPrototypeOf(proto) as { clearUpdateRanges(this: unknown): void };
  Object.assign(proto, {
    _rangeCycle: 0,
    addUniformUpdateRange(this: RangeGroup, uniform: Uniform) {
      let range = this._updateRangeCache.get(uniform.index);
      if (range === undefined) { range = { start: 0, count: 0, cycle: -1 }; this._updateRangeCache.set(uniform.index, range); }
      if (range.cycle === this._rangeCycle) return;
      range.cycle = this._rangeCycle;
      range.start = uniform.offset;
      range.count = uniform.itemSize;
      this.updateRanges.push(range);
    },
    clearUpdateRanges(this: RangeGroup) {
      this._rangeCycle++;
      parent.clearUpdateRanges.call(this);
    },
  });
}

/** The class is internal to three/webgpu: patch its prototype from the first uniforms group the renderer updates. */
export function stampRendererUniformRanges(renderer: THREE.WebGPURenderer) {
  const bindings = (renderer as unknown as { _bindings: { _update(group: { bindings: object[] }, list: unknown): unknown } })._bindings;
  const update = bindings._update;
  bindings._update = function (group, list) {
    for (const binding of group.bindings) {
      let proto: object | null = binding;
      while (proto && !Object.hasOwn(proto, 'addUniformUpdateRange')) proto = Object.getPrototypeOf(proto);
      if (proto) { stampUniformRanges(proto); bindings._update = update; break; }
    }
    return update.call(this, group, list);
  };
}
