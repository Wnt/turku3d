import { expect, test } from 'vitest';
// @ts-expect-error r186 renderer internals have no DefinitelyTyped declarations.
import WebGPUBindingUtils from 'three/src/renderers/webgpu/utils/WebGPUBindingUtils.js';
import { poolUniformBuffers } from '../../src/render/uniformPool';

/* eslint-disable @typescript-eslint/no-explicit-any */
/** A recording WebGPU device and an r186-shaped backend around the real WebGPUBindingUtils. */
function setup() {
  const buffers: { size: number; label?: string }[] = [], writes: any[][] = [], groups: any[] = [], order: string[] = [];
  const device: any = {
    limits: { minUniformBufferOffsetAlignment: 256 },
    createBuffer: (descriptor: { size: number; label?: string }) => {
      const buffer = { ...descriptor, id: buffers.length, memory: new Uint8Array(descriptor.size) }; buffers.push(buffer); return buffer;
    },
    createBindGroup: (descriptor: any) => { groups.push(descriptor.entries.map((e: any) => ({ ...e, resource: { ...e.resource } }))); return { id: groups.length }; },
    queue: { writeBuffer: (...args: any[]) => {
      writes.push(args); order.push('write');
      const [buffer, offset, data, dataOffset = 0, size] = args;
      const element = ArrayBuffer.isView(data) ? (data as any).BYTES_PER_ELEMENT ?? 1 : 1;
      const view = ArrayBuffer.isView(data) ? data : new Uint8Array(data);
      const bytes = new Uint8Array(view.buffer, view.byteOffset + dataOffset * element, (size ?? view.byteLength / element - dataOffset) * element);
      buffer.memory.set(bytes, offset);
    }, submit: () => { order.push('submit'); } },
  };
  const data = new WeakMap<object, any>();
  const backend: any = {
    device, get: (o: object) => { let d = data.get(o); if (!d) data.set(o, d = {}); return d; }, delete: (o: object) => data.delete(o),
    createUniformBuffer(binding: any) { const d = this.get(binding); if (d.buffer === undefined) d.buffer = device.createBuffer({ size: binding.byteLength, label: 'own' }); },
    destroyUniformBuffer(binding: any) { this.delete(binding); },
  };
  backend.bindingUtils = new WebGPUBindingUtils(backend);
  let frame = 0;
  const pool = poolUniformBuffers(backend, () => frame)!;
  const uniform = (byteLength: number) => ({ isUniformBuffer: true, byteLength, buffer: new Float32Array(byteLength / 4), updateRanges: [] as { start: number; count: number }[] });
  return { device, backend, pool, buffers, writes, groups, order, uniform, advance: (n: number) => { frame += n; } };
}

test('uniform bindings share 1 MB pages in 256-byte slots; bind groups and writes address their slot', () => {
  const { backend, pool, buffers, writes, groups, uniform } = setup();
  const a = uniform(208), b = uniform(208), big = uniform(32 << 10), wide = uniform(600);
  for (const binding of [a, b, big, wide]) backend.createUniformBuffer(binding);
  const slot = (binding: object) => backend.get(binding).pooled;
  expect(slot(a).page).toBe(slot(b).page);
  expect([slot(a).offset, slot(b).offset]).toEqual([0, 256]);
  expect(slot(wide).size).toBe(1024); // its own size class and page
  expect(slot(big)).toBeUndefined(); // over 16 KB: its own buffer, as r186 does
  expect(buffers.map(x => x.size)).toEqual([1 << 20, 32 << 10, 1 << 20]);
  // The real r186 bind group code; the pool adds offset and size to pooled entries only.
  backend.bindingUtils.createBindGroup({ name: 'object', bindings: [b, big] }, {});
  expect(groups[0]).toEqual([{ binding: 0, resource: { buffer: slot(b).page, offset: 256, size: 208 } },
    { binding: 1, resource: { buffer: backend.get(big).buffer } }]);
  // Whole and ranged writes land at the slot's offset (ranges in elements, offsets in bytes), staged until the submit.
  (b.buffer as Float32Array).set([1, 2, 3, 4, 5, 6, 7, 8]); backend.bindingUtils.updateBinding(b);
  expect(writes).toHaveLength(0);
  (b.buffer as Float32Array).set([9, 10, 11, 12, 13, 14, 15, 16], 4); (b.buffer as Float32Array)[20] = 21; (b.buffer as Float32Array)[21] = 22;
  b.updateRanges.push({ start: 4, count: 4 }, { start: 8, count: 4 }, { start: 20, count: 2 });
  backend.bindingUtils.updateBinding(b);
  backend.device.queue.submit([]);
  expect(writes.map(w => [w[1], w[3], w[4]])).toEqual([[256, 256, 256]]); // one run: slot b's granule
  const gpu = new Float32Array(slot(b).page.memory.buffer, 256, 52);
  expect([...gpu.subarray(0, 12)]).toEqual([1, 2, 3, 4, 9, 10, 11, 12, 13, 14, 15, 16]);
  expect([...gpu.subarray(20, 22)]).toEqual([21, 22]);
  expect(pool.stats).toMatchObject({ pages: 2, slots: 3, dedicated: 1 });
});

test('every binding written before a submit reaches the GPU in a few merged writes, before that submit', () => {
  const { backend, device, uniform, writes, order } = setup();
  const bindings = Array.from({ length: 100 }, () => uniform(208));
  for (const binding of bindings) backend.createUniformBuffer(binding);
  bindings.forEach((binding, i) => { (binding.buffer as Float32Array)[0] = i + 1; if (i % 3) backend.bindingUtils.updateBinding(binding); });
  expect(writes).toHaveLength(0);
  device.queue.submit([]);
  // 66 updates in one page, the untouched slots between them bridged (their mirror bytes equal the GPU's): one write,
  // issued before the submit it serves.
  expect(writes).toHaveLength(1);
  expect(order).toEqual(['write', 'submit']);
  const page = backend.get(bindings[0]).pooled.page;
  bindings.forEach((binding, i) => {
    const value = new Float32Array(page.memory.buffer, backend.get(binding).pooled.offset, 1)[0];
    expect(value).toBe(i % 3 ? i + 1 : 0);
  });
  device.queue.submit([]); // nothing staged: no write
  expect(writes).toHaveLength(1);
});

test('a reused slot starts at zero: r186 never uploads a uniform still equal to its zero-initialised CPU copy', () => {
  const { backend, uniform, advance, writes } = setup();
  const old = uniform(64); backend.createUniformBuffer(old);
  (old.buffer as Float32Array).fill(7); backend.bindingUtils.updateBinding(old); // the old owner's values
  backend.device.queue.submit([]);
  const slot = backend.get(old).pooled;
  backend.destroyUniformBuffer(old);
  advance(3);
  const next = uniform(64); backend.createUniformBuffer(next);
  expect(backend.get(next).pooled).toBe(slot);
  // The new owner's uniforms are all 0, so r186 writes nothing for them; the slot must read zeros, not 7s.
  backend.device.queue.submit([]);
  expect([...slot.page.memory.subarray(slot.offset, slot.offset + 64)].every((b: number) => b === 0)).toBe(true);
  expect(writes.at(-1)!.slice(1, 2)).toEqual([slot.offset]);
});

test('freed slots wait for a GPU completion fence issued after the free', async () => {
  const { device, backend, uniform, advance } = setup();
  let resolve!: () => void;
  device.queue.onSubmittedWorkDone = () => new Promise<void>(r => { resolve = r; });
  const a = uniform(64); backend.createUniformBuffer(a);
  const slot = backend.get(a).pooled;
  backend.destroyUniformBuffer(a);
  advance(10);
  const b = uniform(64); backend.createUniformBuffer(b);
  expect(backend.get(b).pooled).not.toBe(slot); // frames passed, but the GPU has not confirmed
  resolve(); await Promise.resolve();
  const c = uniform(64); backend.createUniformBuffer(c);
  expect(backend.get(c).pooled).toBe(slot);
});

test('a freed slot is reused only after the quarantine, never by a write that could reach an unsubmitted draw', () => {
  const { backend, pool, uniform, advance } = setup();
  const a = uniform(64); backend.createUniformBuffer(a);
  const first = backend.get(a).pooled;
  backend.destroyUniformBuffer(a);
  const b = uniform(64); backend.createUniformBuffer(b);
  expect(backend.get(b).pooled.offset).not.toBe(first.offset);
  advance(3);
  const c = uniform(100); backend.createUniformBuffer(c);
  expect(backend.get(c).pooled).toBe(first);
  expect(first.bytes).toBe(100);
  pool.release();
  const d = uniform(64); backend.createUniformBuffer(d);
  expect(backend.get(d).pooled).toBeUndefined();
});
