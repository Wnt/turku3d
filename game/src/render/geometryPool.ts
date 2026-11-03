// Small static vertex and index buffers sub-allocated from shared pages. r186 creates one GPUBuffer per attribute (or
// interleaved buffer) and index; D3D12 (Dawn) places each in a 64 KB allocation: winperf (Radeon Pro 560X, 4 GB)
// counted 8,081 of 9,255 vertex/index buffers under 64 KB, 591 MB of data taking 1,064 MB on the card.
// Here a static, non-storage buffer up to 64 KB gets a 256-byte-aligned slot in a 4 MB page, and draws bind it with
// its offset and size (setVertexBuffer / setIndexBuffer through a per-draw encoder proxy around r186's _draw).
// r186's own upload code still runs: its 16-bit widening and stride padding fill a mapped stand-in whose unmap writes
// the slot. Updates go through r186's updateAttribute with the slot's offset added. Freed slots wait
// QUARANTINE_FRAMES and a queue.onSubmittedWorkDone() issued after the free (gpuFence) before reuse within their size
// class, so the GPU has finished every draw that read them. A new owner writes its whole range at creation. Pages are never returned, so they stay at the high-water mark of pooled bytes.

import { gpuFence } from './gpuFence';

const PAGE_BYTES = 4 << 20;
const MAX_POOLED_BYTES = 64 << 10;
const ALIGN = 256;
const QUARANTINE_FRAMES = 3;
const STATIC_DRAW = 35044;
// GPUBufferUsage (no global in Node tests).
const INDEX = 0x10, VERTEX = 0x20, COPY_SRC = 0x04, COPY_DST = 0x08;
const VERTEX_UPLOAD = VERTEX | COPY_SRC | COPY_DST, INDEX_UPLOAD = INDEX | COPY_SRC | COPY_DST;

interface Slot { page: GPUBuffer; offset: number; bytes: number; size: number }
interface Target { usage?: number; isStorageBufferAttribute?: boolean; isStorageInstancedBufferAttribute?: boolean }
interface Data { buffer?: GPUBuffer; pooled?: Slot }
interface Encoder {
  setPipeline(pipeline: GPURenderPipeline): void; setBindGroup(index: number, group: GPUBindGroup | null): void;
  setIndexBuffer(buffer: GPUBuffer, format: GPUIndexFormat, offset?: number, size?: number): void;
  setVertexBuffer(slot: number, buffer: GPUBuffer | null, offset?: number, size?: number): void;
  setStencilReference(reference: number): void; setBlendConstant(color: GPUColor): void;
  draw(a: number, b?: number, c?: number, d?: number): void; drawIndexed(a: number, b?: number, c?: number, d?: number, e?: number): void;
  drawIndirect(buffer: GPUBuffer, offset: number): void; drawIndexedIndirect(buffer: GPUBuffer, offset: number): void;
}
interface Backend {
  device?: GPUDevice;
  get(target: object): Data;
  delete(target: object): void;
  _draw(...args: unknown[]): void;
  attributeUtils: {
    _getBufferAttribute(attribute: object): Target;
    createAttribute(attribute: object, usage: number): void;
    updateAttribute(attribute: object): void;
    destroyAttribute(attribute: object): void;
  };
}

/** Pooled bytes of an uploaded vertex/index target, for the GPU meter (world/gpuBytes.ts); undefined when not pooled. */
const pooledTargets = new WeakMap<object, Slot>();
export function pooledGeometryBytes(target: object) { return pooledTargets.get(target)?.size; }

export interface GeometryPoolStats { pages: number; slots: number; free: number; bytes: number; slotBytes: number }

export function poolGeometryBuffers(backend: Backend, frame: () => number) {
  const device = backend.device;
  if (!device) return null;
  const stats: GeometryPoolStats = { pages: 0, slots: 0, free: 0, bytes: 0, slotBytes: 0 };
  let page: { buffer: GPUBuffer; used: number } | null = null;
  const free = new Map<number, Slot[]>(), quarantine: { slot: Slot; frame: number; epoch: number }[] = [];
  const fence = gpuFence(device);
  // Size classes: 256-byte steps to 4 KB, then about 12 % geometric steps (rounded to 256), so reuse wastes little.
  const classOf = (bytes: number) => {
    let size = Math.ceil(bytes / ALIGN) * ALIGN;
    if (size <= 4096) return size;
    let step = 4096;
    while (step < size) step = Math.ceil(step * 1.125 / ALIGN) * ALIGN;
    return step;
  };
  const allocate = (bytes: number): Slot => {
    const size = classOf(bytes), now = frame();
    if (quarantine.length) fence.request();
    while (quarantine.length && now - quarantine[0].frame >= QUARANTINE_FRAMES && fence.done(quarantine[0].epoch)) {
      const { slot } = quarantine.shift()!;
      let list = free.get(slot.size);
      if (!list) free.set(slot.size, list = []);
      list.push(slot);
    }
    const reused = free.get(size)?.pop();
    if (reused) { reused.bytes = bytes; stats.free--; stats.slots++; stats.slotBytes += size; return reused; }
    if (!page || page.used + size > PAGE_BYTES) {
      page = { buffer: device.createBuffer({ label: 'geometryPool', size: PAGE_BYTES, usage: VERTEX | INDEX | COPY_SRC | COPY_DST }), used: 0 };
      stats.pages++; stats.bytes += PAGE_BYTES;
    }
    const slot = { page: page.buffer, offset: page.used, bytes, size };
    page.used += size; stats.slots++; stats.slotBytes += size;
    return slot;
  };

  const utils = backend.attributeUtils;
  const create = utils.createAttribute.bind(utils), update = utils.updateAttribute.bind(utils), destroy = utils.destroyAttribute.bind(utils);

  utils.createAttribute = (attribute, usage) => {
    const target = utils._getBufferAttribute(attribute), data = backend.get(target);
    if (data.buffer !== undefined) return;
    const poolable = (usage === VERTEX_UPLOAD || usage === INDEX_UPLOAD) && (target.usage ?? STATIC_DRAW) === STATIC_DRAW
      && !target.isStorageBufferAttribute && !target.isStorageInstancedBufferAttribute;
    if (!poolable) { create(attribute, usage); return; }
    let slot: Slot | null = null;
    const createBuffer = device.createBuffer;
    device.createBuffer = function (this: GPUDevice, descriptor: GPUBufferDescriptor) {
      if (slot || !descriptor.mappedAtCreation || descriptor.size > MAX_POOLED_BYTES) return createBuffer.call(this, descriptor);
      const own = slot = allocate(descriptor.size), scratch = new ArrayBuffer(descriptor.size);
      return { size: descriptor.size, getMappedRange: () => scratch, unmap: () => device.queue.writeBuffer(own.page, own.offset, scratch),
        destroy: () => {} } as unknown as GPUBuffer;
    };
    try { create(attribute, usage); } finally { device.createBuffer = createBuffer; }
    if (slot) { data.buffer = (slot as Slot).page; data.pooled = slot; pooledTargets.set(target, slot); }
  };

  // r186 writes at buffer offsets of its own; a pooled target's writes move by its slot offset.
  utils.updateAttribute = attribute => {
    const slot = backend.get(utils._getBufferAttribute(attribute)).pooled;
    if (!slot) { update(attribute); return; }
    const queue = device.queue, write = queue.writeBuffer;
    queue.writeBuffer = function (this: GPUQueue, buffer: GPUBuffer, offset: number, ...rest: unknown[]) {
      return (write as (...args: unknown[]) => void).call(this, buffer, buffer === slot.page ? slot.offset + offset : offset, ...rest);
    } as GPUQueue['writeBuffer'];
    try { update(attribute); } finally { queue.writeBuffer = write; }
  };

  utils.destroyAttribute = attribute => {
    const target = utils._getBufferAttribute(attribute), data = backend.get(target);
    if (data.pooled) {
      quarantine.push({ slot: data.pooled, frame: frame(), epoch: fence.after() }); fence.request();
      stats.slots--; stats.free++; stats.slotBytes -= data.pooled.size;
      pooledTargets.delete(target);
      data.pooled = undefined; data.buffer = undefined;
      backend.delete(attribute);
      return;
    }
    // A sibling of an interleaved buffer whose pooled slot is already freed, or a never-uploaded attribute.
    if (data.buffer === undefined) { backend.delete(attribute); return; }
    destroy(attribute);
  };

  // One proxy for every draw: r186's _draw binds whole buffers; pooled ones need their range.
  const proxy = {
    real: null as unknown as Encoder, buffers: null as unknown as readonly object[], index: null as object | null,
    setPipeline(pipeline: GPURenderPipeline) { this.real.setPipeline(pipeline); },
    setBindGroup(index: number, group: GPUBindGroup | null) { this.real.setBindGroup(index, group); },
    setIndexBuffer(buffer: GPUBuffer, format: GPUIndexFormat) {
      const slot = this.index ? backend.get(this.index).pooled : undefined;
      if (slot) this.real.setIndexBuffer(slot.page, format, slot.offset, slot.bytes); else this.real.setIndexBuffer(buffer, format);
    },
    setVertexBuffer(i: number, buffer: GPUBuffer) {
      const slot = backend.get(this.buffers[i]).pooled;
      if (slot) this.real.setVertexBuffer(i, slot.page, slot.offset, slot.bytes); else this.real.setVertexBuffer(i, buffer);
    },
    setStencilReference(reference: number) { this.real.setStencilReference(reference); },
    setBlendConstant(color: GPUColor) { this.real.setBlendConstant(color); },
    draw(a: number, b?: number, c?: number, d?: number) { this.real.draw(a, b, c, d); },
    drawIndexed(a: number, b?: number, c?: number, d?: number, e?: number) { this.real.drawIndexed(a, b, c, d, e); },
    drawIndirect(buffer: GPUBuffer, offset: number) { this.real.drawIndirect(buffer, offset); },
    drawIndexedIndirect(buffer: GPUBuffer, offset: number) { this.real.drawIndexedIndirect(buffer, offset); },
  };
  const draw = backend._draw;
  // r186: _draw(renderObject, info, renderContextData, pipelineGPU, bindings, vertexBuffers, drawParams, passEncoderGPU, ...)
  // Explicit parameters: no per-draw array.
  backend._draw = function (this: Backend, renderObject: unknown, info: unknown, renderContextData: unknown, pipelineGPU: unknown,
    bindings: unknown, vertexBuffers: unknown, drawParams: unknown, passEncoderGPU: unknown, currentSets: unknown, cameraIndexSlot: unknown) {
    proxy.real = passEncoderGPU as Encoder; proxy.buffers = vertexBuffers as object[];
    proxy.index = (renderObject as { getIndex(): object | null }).getIndex();
    if (cameraIndexSlot === undefined) draw.call(this, renderObject, info, renderContextData, pipelineGPU, bindings, vertexBuffers, drawParams, proxy, currentSets);
    else draw.call(this, renderObject, info, renderContextData, pipelineGPU, bindings, vertexBuffers, drawParams, proxy, currentSets, cameraIndexSlot);
  };

  return {
    stats,
    release() {
      utils.createAttribute = create; utils.updateAttribute = update; utils.destroyAttribute = destroy; backend._draw = draw;
    },
  };
}
