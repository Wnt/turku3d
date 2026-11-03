import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
// @ts-expect-error r186 renderer internals have no DefinitelyTyped declarations.
import WebGPUAttributeUtils from 'three/src/renderers/webgpu/utils/WebGPUAttributeUtils.js';
import WebGPUBackend from 'three/src/renderers/webgpu/WebGPUBackend.js';
import { poolGeometryBuffers, pooledGeometryBytes } from '../../src/render/geometryPool';
import { GpuMeter } from '../../src/world/gpuBytes';

/* eslint-disable @typescript-eslint/no-explicit-any */
const VERTEX = 0x20 | 0x04 | 0x08, INDEX = 0x10 | 0x04 | 0x08;

function setup() {
  vi.stubGlobal('GPUBufferUsage', { MAP_READ: 1, MAP_WRITE: 2, COPY_SRC: 4, COPY_DST: 8, INDEX: 16, VERTEX: 32, UNIFORM: 64, STORAGE: 128, INDIRECT: 256 });
  const buffers: any[] = [], writes: any[][] = [];
  const device: any = {
    createBuffer: (d: any) => {
      const bytes = new ArrayBuffer(d.size);
      const b = { ...d, bytes, destroyed: false, getMappedRange: () => bytes, unmap: () => {}, destroy() { this.destroyed = true; } };
      buffers.push(b); return b;
    },
    queue: { writeBuffer: (buffer: any, offset: number, data: any, dataOffset = 0, size?: number) => {
      const view = ArrayBuffer.isView(data) ? data : new Uint8Array(data);
      const element = ArrayBuffer.isView(data) ? (data as any).BYTES_PER_ELEMENT ?? 1 : 1;
      const from = new Uint8Array(view.buffer, view.byteOffset + dataOffset * element, (size ?? (view.byteLength / element - dataOffset)) * element);
      new Uint8Array(buffer.bytes, offset, from.byteLength).set(from);
      writes.push([buffer, offset, from.byteLength]);
    } },
  };
  const data = new WeakMap<object, any>();
  const encoder: any[][] = [];
  const backend: any = {
    device, get: (o: object) => { let d = data.get(o); if (!d) data.set(o, d = {}); return d; }, delete: (o: object) => data.delete(o),
    // r186's _draw binds whole buffers through the encoder it is given (WebGPUBackend._draw, parameter 8).
    _draw(renderObject: any, _info: any, _data: any, _pipeline: any, _bindings: any, vertexBuffers: any[], _params: any, pass: any) {
      const index = renderObject.getIndex();
      if (index) pass.setIndexBuffer(this.get(index).buffer, 'uint32');
      vertexBuffers.forEach((vb, i) => pass.setVertexBuffer(i, this.get(vb).buffer));
      pass.drawIndexed(3);
    },
  };
  backend.attributeUtils = new WebGPUAttributeUtils(backend);
  let frame = 0;
  const pool = poolGeometryBuffers(backend, () => frame)!;
  const pass = { setIndexBuffer: (...a: any[]) => encoder.push(['index', ...a]), setVertexBuffer: (...a: any[]) => encoder.push(['vertex', ...a]),
    drawIndexed: (...a: any[]) => encoder.push(['draw', ...a]) };
  return { device, backend, pool, buffers, writes, encoder, pass, advance: (n: number) => { frame += n; } };
}

test("r186's _draw still takes the pass encoder as its eighth parameter", () => {
  const draw = (WebGPUBackend.prototype as unknown as { _draw: (...args: unknown[]) => void })._draw;
  expect(draw.length).toBe(9);
  expect(String(draw).replace(/\s+/g, ' ')).toMatch(/^_draw\( renderObject, info, renderContextData, pipelineGPU, bindings, vertexBuffers, drawParams, passEncoderGPU, currentSets,/);
});

test('small static buffers share a page, with r186 conversion and padding; draws bind them with offsets', () => {
  const { backend, pool, buffers, encoder, pass } = setup();
  const utils = backend.attributeUtils;
  const position = new THREE.Float32BufferAttribute([0, 1, 2, 3, 4, 5, 6, 7, 8], 3);
  const index = new THREE.Uint16BufferAttribute([0, 1, 2], 1);
  const uv = new THREE.Float32BufferAttribute(new Float32Array(6).fill(0.5), 2);
  const dynamic = new THREE.Float32BufferAttribute(new Float32Array(9), 3).setUsage(THREE.DynamicDrawUsage);
  const big = new THREE.Float32BufferAttribute(new Float32Array(32 * 1024), 1);
  utils.createAttribute(position, VERTEX); utils.createAttribute(index, INDEX); utils.createAttribute(uv, VERTEX);
  utils.createAttribute(dynamic, VERTEX); utils.createAttribute(big, VERTEX);
  const page = buffers[0];
  expect(page.label).toBe('geometryPool');
  const slot = (a: object) => backend.get(a).pooled;
  expect([slot(position).offset, slot(index).offset, slot(uv).offset]).toEqual([0, 256, 512]);
  expect([slot(dynamic), slot(big)]).toEqual([undefined, undefined]); // their own buffers, as before
  expect(buffers.length).toBe(3);
  // The page holds exactly what r186 uploads: the position floats and the index widened to Uint32.
  expect([...new Float32Array(page.bytes, 0, 9)]).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8]);
  expect([...new Uint32Array(page.bytes, 256, 3)]).toEqual([0, 1, 2]);
  expect(index.array).toBeInstanceOf(Uint32Array);
  // Draws bind each pooled buffer by its range; others whole.
  const renderObject = { getIndex: () => index };
  backend._draw(renderObject, null, null, null, null, [position, dynamic], null, pass, {});
  const name = (b: any) => b === page ? 'page' : b === backend.get(dynamic).buffer ? 'dynamic' : b;
  // Trailing undefined arguments are WebIDL defaults for a real encoder.
  const trim = (call: any[]) => { const out = call.map(name); while (out.at(-1) === undefined) out.pop(); return out; };
  expect(encoder.map(trim)).toEqual([['index', 'page', 'uint32', 256, 12], ['vertex', 0, 'page', 0, 36], ['vertex', 1, 'dynamic'], ['draw', 3]]);
  // Updates land in the slot (r186 range writes: element offsets, byte buffer offsets).
  position.array[4] = 40; position.addUpdateRange(3, 3); utils.updateAttribute(position);
  expect(new Float32Array(page.bytes, 0, 9)[4]).toBe(40);
  expect(pooledGeometryBytes(position)).toBe(256);
  expect(pool.stats).toMatchObject({ pages: 1, slots: 3 });
});

test('interleaved buffers pool once; freeing waits out the quarantine and never destroys the page', () => {
  const { backend, buffers, advance } = setup();
  const utils = backend.attributeUtils;
  const interleaved = new THREE.InterleavedBuffer(new Float32Array(5 * 3), 5);
  const p = new THREE.InterleavedBufferAttribute(interleaved, 3, 0), u = new THREE.InterleavedBufferAttribute(interleaved, 2, 3);
  utils.createAttribute(p, VERTEX); utils.createAttribute(u, VERTEX);
  const first = backend.get(interleaved).pooled;
  expect(first.offset).toBe(0);
  utils.destroyAttribute(p); utils.destroyAttribute(u);
  expect(buffers[0].destroyed).toBe(false);
  const next = new THREE.Float32BufferAttribute(new Float32Array(15), 3);
  utils.createAttribute(next, VERTEX);
  expect(backend.get(next).pooled.offset).toBe(256); // the freed slot is still in quarantine
  advance(3);
  const later = new THREE.Float32BufferAttribute(new Float32Array(15), 3);
  utils.createAttribute(later, VERTEX);
  expect(backend.get(later).pooled).toBe(first);
});

test('the GPU meter counts a pooled buffer by its slot, not a 64 KiB placement', () => {
  const { backend } = setup();
  const mesh = new THREE.Mesh(new THREE.BufferGeometry());
  mesh.geometry.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(30), 3));
  expect(new GpuMeter().mesh(mesh)).toBe(64 * 1024);
  backend.attributeUtils.createAttribute(mesh.geometry.getAttribute('position'), VERTEX);
  expect(new GpuMeter().mesh(mesh)).toBe(256);
});
