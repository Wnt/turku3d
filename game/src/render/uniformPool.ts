// Uniform buffers sub-allocated from shared pages. r186 creates one GPUBuffer per uniform binding, so one per render
// object for the object group. D3D12 (Dawn) places each such small buffer in its own 64 KB allocation: winperf (Radeon
// Pro 560X, 4 GB) counted 5,526 uniform buffers holding at least 345 MB, with the card at 3.96–4.0 GB at peak. Here
// every uniform binding gets a 256-byte-aligned slot in a 1 MB page of its size class, and its bind group entry
// carries the slot's offset and size. The values and the write pattern (r186's updateBinding ranges) are unchanged.
// Freed slots wait QUARANTINE_FRAMES and a queue.onSubmittedWorkDone() issued after the free (gpuFence), so the GPU has
// finished every draw that read them before a new owner writes them.
// A reused slot is zeroed. r186's uniform groups upload only values that differ from their CPU copy, which starts at
// zero because a new GPU buffer is zero-filled; a reused slot still held its previous owner's bytes, so every uniform
// whose value is 0 read that garbage. That hung the GPU on Jonni's M1 Max (Metal command-buffer hang, device lost).
// Writes are staged: each page keeps a CPU mirror, a binding's update copies its bytes there, and the dirty 256-byte
// granules go to the GPU as merged runs right before the next queue.submit. r186 wrote every binding range with its
// own queue.writeBuffer: 1,732 calls per frame on the Windows laptop while driving (23 submits a frame), each a
// separate command through Chrome's GPU process. A submit's command buffers see every write issued before it, and
// within one submit only the last value of a slot is ever read, so staging up to the submit reads the same bytes.

import { gpuFence } from './gpuFence';

const PAGE_BYTES = 1 << 20;
/** Bindings above this get their own buffer, as before. */
const MAX_POOLED_BYTES = 16 << 10;
const QUARANTINE_FRAMES = 3;
const UNIFORM = 0x40, COPY_DST = 0x08; // GPUBufferUsage.UNIFORM | COPY_DST (no global in Node tests)
/** Staging granule (bytes, the slot alignment) and the clean gap (granules) a merged upload run may bridge. */
const GRANULE = 256, GAP_GRANULES = 4;

/** A page's CPU mirror (the bytes the GPU copy will hold after the next flush) and its dirty granules. */
interface Stage { buffer: GPUBuffer; bytes: Uint8Array; words: Uint32Array; dirty: Uint8Array; low: number; high: number; queued: boolean }
interface Slot { page: GPUBuffer; offset: number; bytes: number; size: number; stage: Stage }
interface Binding { byteLength: number; buffer: ArrayBufferView | ArrayBuffer; updateRanges: { start: number; count: number }[];
  isUniformBuffer?: boolean }
interface BindingData { buffer?: GPUBuffer; pooled?: Slot }
interface Backend {
  device?: GPUDevice;
  get(target: object): BindingData;
  delete(target: object): void;
  createUniformBuffer(binding: Binding): void;
  destroyUniformBuffer(binding: Binding): void;
  bindingUtils: { updateBinding(binding: Binding): void; createBindGroup(bindGroup: { bindings: Binding[] }, layout: GPUBindGroupLayout): GPUBindGroup };
}

export interface UniformPoolStats { pages: number; slots: number; free: number; dedicated: number; bytes: number }

export function poolUniformBuffers(backend: Backend, frame: () => number) {
  const device = backend.device;
  if (!device) return null;
  const align = Math.max(256, device.limits?.minUniformBufferOffsetAlignment ?? 256);
  const open = new Map<number, { buffer: GPUBuffer; used: number; stage: Stage }>();
  const free = new Map<number, Slot[]>();
  const quarantine: { slot: Slot; frame: number; epoch: number }[] = [];
  const fence = gpuFence(device);
  const stats: UniformPoolStats = { pages: 0, slots: 0, free: 0, dedicated: 0, bytes: 0 };
  const classOf = (bytes: number) => { let size = align; while (size < bytes) size *= 2; return size; };
  const queue = device.queue, submit = queue.submit.bind(queue);
  const dirtyStages: Stage[] = [];
  const mark = (stage: Stage, start: number, end: number) => {
    const first = (start / GRANULE) | 0, last = ((end - 1) / GRANULE) | 0;
    for (let g = first; g <= last; g++) stage.dirty[g] = 1;
    if (first < stage.low) stage.low = first;
    if (last > stage.high) stage.high = last;
    if (!stage.queued) { stage.queued = true; dirtyStages.push(stage); }
  };
  /** Uploads every dirty run of every staged page: one writeBuffer per run of dirty granules (clean gaps up to
   *  GAP_GRANULES bridged). */
  const flush = () => {
    for (let i = 0; i < dirtyStages.length; i++) {
      const stage = dirtyStages[i], dirty = stage.dirty;
      let g = stage.low;
      while (g <= stage.high) {
        if (!dirty[g]) { g++; continue; }
        const start = g;
        let end = g, clean = 0;
        for (g++; g <= stage.high && clean <= GAP_GRANULES; g++) {
          if (dirty[g]) { end = g; clean = 0; } else clean++;
        }
        dirty.fill(0, start, end + 1);
        queue.writeBuffer(stage.buffer, start * GRANULE, stage.bytes, start * GRANULE, (end + 1 - start) * GRANULE);
        g = end + 1;
      }
      stage.low = Infinity; stage.high = -1; stage.queued = false;
    }
    dirtyStages.length = 0;
  };
  queue.submit = (buffers: Iterable<GPUCommandBuffer>) => { if (dirtyStages.length) flush(); return submit(buffers); };
  /** 4-byte views of the bindings' CPU arrays (typed arrays of 32-bit elements), made once per array. */
  const words = new WeakMap<ArrayBufferView, Uint32Array>(), bytesOf = new WeakMap<ArrayBufferView, Uint8Array>();
  const wordView = (array: ArrayBufferView) => {
    let view = words.get(array);
    if (!view) { view = new Uint32Array(array.buffer, array.byteOffset, array.byteLength >> 2); words.set(array, view); }
    return view;
  };
  const byteView = (array: ArrayBufferView) => {
    let view = bytesOf.get(array);
    if (!view) { view = new Uint8Array(array.buffer, array.byteOffset, array.byteLength); bytesOf.set(array, view); }
    return view;
  };

  const allocate = (bytes: number): Slot => {
    const size = classOf(bytes), now = frame();
    while (quarantine.length && now - quarantine[0].frame >= QUARANTINE_FRAMES && fence.done(quarantine[0].epoch)) {
      const { slot } = quarantine.shift()!;
      let list = free.get(slot.size);
      if (!list) free.set(slot.size, list = []);
      list.push(slot);
    }
    if (quarantine.length) fence.request();
    const reused = free.get(size)?.pop();
    if (reused) {
      // Back to the zeros r186 assumes of a new buffer (it never uploads a uniform still equal to them).
      reused.stage.bytes.fill(0, reused.offset, reused.offset + reused.size);
      mark(reused.stage, reused.offset, reused.offset + reused.size);
      reused.bytes = bytes; stats.free--; stats.slots++; return reused;
    }
    let page = open.get(size);
    if (!page || page.used + size > PAGE_BYTES) {
      const buffer = device.createBuffer({ label: `uniformPool${size}`, size: PAGE_BYTES, usage: UNIFORM | COPY_DST }), mirror = new Uint8Array(PAGE_BYTES);
      page = { buffer, used: 0, stage: { buffer, bytes: mirror, words: new Uint32Array(mirror.buffer), dirty: new Uint8Array(PAGE_BYTES / GRANULE), low: Infinity, high: -1, queued: false } };
      open.set(size, page); stats.pages++; stats.bytes += PAGE_BYTES;
    }
    const slot = { page: page.buffer, offset: page.used, bytes, size, stage: page.stage };
    page.used += size; stats.slots++;
    return slot;
  };

  const create = backend.createUniformBuffer.bind(backend), destroy = backend.destroyUniformBuffer.bind(backend);
  backend.createUniformBuffer = binding => {
    const data = backend.get(binding);
    if (data.buffer !== undefined) return;
    if (binding.byteLength > MAX_POOLED_BYTES) { stats.dedicated++; create(binding); return; }
    const slot = allocate(binding.byteLength);
    data.buffer = slot.page; data.pooled = slot;
  };
  backend.destroyUniformBuffer = binding => {
    const slot = backend.get(binding).pooled;
    if (!slot) { stats.dedicated--; destroy(binding); return; }
    backend.delete(binding);
    quarantine.push({ slot, frame: frame(), epoch: fence.after() }); fence.request(); stats.slots--; stats.free++;
  };

  const utils = backend.bindingUtils;
  const update = utils.updateBinding.bind(utils);
  // r186 WebGPUBindingUtils.updateBinding, at the slot's offset.
  utils.updateBinding = binding => {
    const data = backend.get(binding), slot = data.pooled;
    if (!slot) {
      // A binding written after its buffer was destroyed (its bind group's last draw left) is a lifetime bug upstream;
      // name it instead of r186's bare "writeBuffer ... parameter 1 is not of type 'GPUBuffer'".
      if (data.buffer === undefined) throw new Error(`uniform binding "${(binding as { name?: string }).name ?? '?'}" written after its buffer was destroyed`);
      update(binding); return;
    }
    const array = binding.buffer as ArrayBufferView, ranges = binding.updateRanges, stage = slot.stage;
    const element = (array as unknown as { BYTES_PER_ELEMENT?: number }).BYTES_PER_ELEMENT ?? 1;
    if (ranges.length === 0) {
      stage.bytes.set(byteView(array), slot.offset);
      mark(stage, slot.offset, slot.offset + array.byteLength);
      return;
    }
    let start = ranges[0].start;
    for (let i = 0; i < ranges.length; i++) {
      const range = ranges[i], next = ranges[i + 1], end = range.start + range.count;
      if (next !== undefined && next.start === end) continue;
      if (element === 4) {
        // Element ranges of a 32-bit array: copied as raw words (bit-exact, no view per write).
        const source = wordView(array), target = stage.words, base = (slot.offset >> 2);
        for (let k = start; k < end; k++) target[base + k] = source[k];
      } else stage.bytes.set(byteView(array).subarray(start * element, end * element), slot.offset + start * element);
      mark(stage, slot.offset + start * element, slot.offset + end * element);
      if (next !== undefined) start = next.start;
    }
  };

  const createBindGroup = utils.createBindGroup.bind(utils);
  utils.createBindGroup = (bindGroup, layout) => {
    const original = device.createBindGroup;
    // r186 numbers entries by binding order (bindingPoint); pooled uniform entries get their slot's range.
    device.createBindGroup = function (this: GPUDevice, descriptor: GPUBindGroupDescriptor) {
      for (const entry of descriptor.entries as GPUBindGroupEntry[]) {
        const binding = bindGroup.bindings[entry.binding];
        const slot = binding?.isUniformBuffer ? backend.get(binding).pooled : undefined;
        if (slot) entry.resource = { buffer: slot.page, offset: slot.offset, size: binding.byteLength };
      }
      return original.call(this, descriptor);
    };
    try { return createBindGroup(bindGroup, layout); }
    finally { device.createBindGroup = original; }
  };

  return {
    stats,
    /** Uploads the staged writes now (queue.submit does it on its own). */
    flush,
    release() {
      flush(); queue.submit = submit;
      backend.createUniformBuffer = create; backend.destroyUniformBuffer = destroy;
      utils.updateBinding = update; utils.createBindGroup = createBindGroup;
    },
  };
}
