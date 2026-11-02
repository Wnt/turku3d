import { BufferGeometry, InterleavedBuffer, InterleavedBufferAttribute } from 'three/webgpu';
import { attribute, varying, vec3, vec4 } from 'three/tsl';
import { PreparationBudget } from './preparationBudget';
import { packProgram, type ProgramField } from './packProgram';

/** Authored facade instructions are constant on each triangle, unlike the surface UVs.
 * Smooth interpolation can move integer module IDs/flags across floor/mod/equality boundaries. */
export function facadeAttribute<const T extends 'vec2' | 'vec3' | 'vec4'>(name: string, type: T) {
  return varying(attribute(name, type), `v${name}`).setInterpolation('flat');
}

/** Four vec3 fields occupy three exact vec4 varyings. The production material
 * also needs shadow/environment varyings and must fit WebGPU's 16-location limit. */
export function facadePaintAttributes() {
  const wall = attribute('_kit_wall', 'vec3'), base = attribute('_kit_base', 'vec3');
  const trim = attribute('_kit_trim', 'vec3'), floors = attribute('_kit_floors', 'vec3');
  const a = varying(vec4(wall, base.x), 'v_kit_paint0').setInterpolation('flat');
  const b = varying(vec4(base.yz, trim.xy), 'v_kit_paint1').setInterpolation('flat');
  const c = varying(vec4(trim.z, floors), 'v_kit_paint2').setInterpolation('flat');
  return { wall: a.xyz, base: vec3(a.w, b.xy), trim: vec3(b.zw, c.x), floors: c.yzw };
}

function programFields(geometry: BufferGeometry) {
  if (!geometry.hasAttribute('_kit_opening')) return null;
  const names = Object.keys(geometry.attributes).filter(name => name.startsWith('_kit_'));
  if (geometry.getAttribute(names[0]) instanceof InterleavedBufferAttribute) return null;
  // Meshopt workers resolve accessors independently; their completion order is not a field layout.
  names.sort();
  const attributes = names.map(name => geometry.getAttribute(name));
  const fields: ProgramField[] = attributes.map(attribute => ({ array: attribute.array,
    itemSize: attribute.itemSize, normalized: attribute.normalized }));
  return { names, fields };
}
function bind(geometry: BufferGeometry, names: string[], fields: ProgramField[], packed: Float32Array) {
  const buffer = new InterleavedBuffer(packed, fields.reduce((sum, field) => sum + field.itemSize, 0));
  let offset = 0;
  fields.forEach((field, index) => {
    geometry.setAttribute(names[index], new InterleavedBufferAttribute(buffer, field.itemSize, offset));
    offset += field.itemSize;
  });
}
/** WebGPU guarantees eight vertex buffers. Pack every program field into one buffer. */
export function interleaveProgram(geometry: BufferGeometry) {
  const program = programFields(geometry);
  if (program) bind(geometry, program.names, program.fields, packProgram(program.fields));
}

let worker: Worker | undefined, sequence = 0;
const pending = new Map<number, { resolve: (packed: Float32Array) => void; reject: (error: Error) => void }>();
const copyBudget = new PreparationBudget();
async function packAsync(fields: ProgramField[]): Promise<Float32Array> {
  const copies: ProgramField[] = [];
  for (const field of fields) {
    copies.push({ ...field, array: field.array.slice() });
    const nextFrame = copyBudget.checkpoint();
    if (nextFrame) await nextFrame;
  }
  if (!worker) {
    worker = new Worker(new URL('./packProgram.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = ({ data }) => {
      const job = pending.get(data.id); pending.delete(data.id);
      if (data.error) job?.reject(new Error(data.error)); else job?.resolve(data.packed);
    };
    worker.onerror = event => {
      for (const job of pending.values()) job.reject(new Error(event.message));
      pending.clear(); worker?.terminate(); worker = undefined;
    };
  }
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    // Transfer only each field's bytes, never the full shared GLB backing buffer or a live attribute.
    worker!.postMessage({ id, fields: copies }, copies.map(field => field.array.buffer as ArrayBuffer));
  });
}
/** Streamed geometry remains hidden until the worker returns its final transferable vertex buffer. */
export async function interleaveProgramAsync(geometry: BufferGeometry) {
  const program = programFields(geometry);
  if (program) bind(geometry, program.names, program.fields, await packAsync(program.fields));
}
