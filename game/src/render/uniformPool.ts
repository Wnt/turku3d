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

import { gpuFence } from './gpuFence';

const PAGE_BYTES = 1 << 20;
/** Bindings above this get their own buffer, as before. */
const MAX_POOLED_BYTES = 16 << 10;
const QUARANTINE_FRAMES = 3;
const UNIFORM = 0x40, COPY_DST = 0x08; // GPUBufferUsage.UNIFORM | COPY_DST (no global in Node tests)

interface Slot { page: GPUBuffer; offset: number; bytes: number; size: number }
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
  const open = new Map<number, { buffer: GPUBuffer; used: number }>();
  const free = new Map<number, Slot[]>();
  const quarantine: { slot: Slot; frame: number; epoch: number }[] = [];
  const fence = gpuFence(device);
  const stats: UniformPoolStats = { pages: 0, slots: 0, free: 0, dedicated: 0, bytes: 0 };
  const classOf = (bytes: number) => { let size = align; while (size < bytes) size *= 2; return size; };

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
      device.queue.writeBuffer(reused.page, reused.offset, new Uint8Array(reused.size));
      reused.bytes = bytes; stats.free--; stats.slots++; return reused;
    }
    let page = open.get(size);
    if (!page || page.used + size > PAGE_BYTES) {
      page = { buffer: device.createBuffer({ label: `uniformPool${size}`, size: PAGE_BYTES, usage: UNIFORM | COPY_DST }), used: 0 };
      open.set(size, page); stats.pages++; stats.bytes += PAGE_BYTES;
    }
    const slot = { page: page.buffer, offset: page.used, bytes, size };
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
    const slot = backend.get(binding).pooled;
    if (!slot) { update(binding); return; }
    const array = binding.buffer, ranges = binding.updateRanges;
    if (ranges.length === 0) { device.queue.writeBuffer(slot.page, slot.offset, array as BufferSource, 0); return; }
    const typed = ArrayBuffer.isView(array) && !(array instanceof DataView);
    const factor = typed ? 1 : (array as unknown as { BYTES_PER_ELEMENT: number }).BYTES_PER_ELEMENT;
    const element = typed ? (array as unknown as { BYTES_PER_ELEMENT: number }).BYTES_PER_ELEMENT : 1;
    let start = ranges[0].start;
    for (let i = 0; i < ranges.length; i++) {
      const range = ranges[i], next = ranges[i + 1], end = range.start + range.count;
      if (next !== undefined && next.start === end) continue;
      const dataOffset = start * factor, size = (end - start) * factor;
      device.queue.writeBuffer(slot.page, slot.offset + dataOffset * element, array as BufferSource, dataOffset, size);
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
    release() {
      backend.createUniformBuffer = create; backend.destroyUniformBuffer = destroy;
      utils.updateBinding = update; utils.createBindGroup = createBindGroup;
    },
  };
}
