import type * as THREE from 'three/webgpu';

type Range = { start: number; count: number; cycle?: number };
interface RangeGroup {
  _updateRangeCache: Map<number, Range>;
  updateRanges: Range[];
}
interface Uniform { index: number; offset: number; itemSize: number }

/** r186 UniformsGroup remembers which uniforms already queued their update range this upload in a Set that
 * clearUpdateRanges empties after every upload (Bindings._update). Clearing a Set gives it a fresh hash table, so every
 * uniform group written in a frame (per draw and pass: object matrices, material values) allocated a new table that
 * lived until the next upload: the largest surviving allocation under _renderObjectDirect in city.lab play
 * (framecheck mac-heap-1: ~1 MB/s of scavenge survivors, each an old-to-new pointer). The cached range objects carry
 * the upload cycle they were queued in instead; the queued ranges and their order are unchanged.
 *
 * The cycle is one counter for every group, advanced by every clear. r186 clones a program's per-object group for each
 * draw with Object.assign (Binding.clone), so all draws of a program share one range cache, one range list and one
 * Set: the Set means "queued since the last upload of any draw of this program". A per-group counter broke that: a
 * draw's fresh group (cycle 0) found the ranges another draw had stamped in its own cycle 0 and queued nothing for
 * them, so a new draw's constant uniforms (paint, fog, material values) never reached the GPU while its matrices did
 * (Jonni 2026-11-08: a car he entered turned pitch black, its aerial fog colour and range read as zero). */
let cycle = 0;
export function stampUniformRanges(proto: object) {
  const parent = Object.getPrototypeOf(proto) as { clearUpdateRanges(this: unknown): void };
  Object.assign(proto, {
    addUniformUpdateRange(this: RangeGroup, uniform: Uniform) {
      let range = this._updateRangeCache.get(uniform.index);
      if (range === undefined) { range = { start: 0, count: 0, cycle: -1 }; this._updateRangeCache.set(uniform.index, range); }
      if (range.cycle === cycle) return;
      range.cycle = cycle;
      range.start = uniform.offset;
      range.count = uniform.itemSize;
      this.updateRanges.push(range);
    },
    clearUpdateRanges(this: RangeGroup) {
      cycle++;
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
