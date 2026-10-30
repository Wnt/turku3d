import * as THREE from 'three/webgpu';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { interleaveProgram } from '../render/facadeProgramAttributes';
import { disposeDraw } from '../render/sharedMaterials';
import { PreparationBudget } from '../render/preparationBudget';

interface Batch { mesh: THREE.Mesh; sources: THREE.Mesh[]; parent: THREE.Object3D }
const owners = new WeakMap<THREE.Object3D, Batch[]>();
const budget = new PreparationBudget();

/** Only the canonical, tightly packed facade layout can bypass the general attribute merger. */
function packedPrograms(sources: THREE.Mesh[]) {
  const names = Object.keys(sources[0].geometry.attributes).filter(name => name.startsWith('_kit_')).sort();
  if (!names.includes('_kit_opening')) return null;
  const fields: { name: string; itemSize: number; offset: number }[] = [];
  let stride = 0;
  for (const name of names) {
    const attribute = sources[0].geometry.getAttribute(name);
    if (!(attribute instanceof THREE.InterleavedBufferAttribute) || attribute.normalized || attribute.offset !== stride) return null;
    fields.push({ name, itemSize: attribute.itemSize, offset: stride });
    stride += attribute.itemSize;
  }
  const buffers: THREE.InterleavedBuffer[] = [];
  for (const { geometry } of sources) {
    const first = geometry.getAttribute(names[0]) as THREE.InterleavedBufferAttribute;
    if (!(first instanceof THREE.InterleavedBufferAttribute)) return null;
    const buffer = first.data;
    if (!(buffer.array instanceof Float32Array) || buffer instanceof THREE.InstancedInterleavedBuffer
      || buffer.stride !== stride || buffer.count !== geometry.attributes.position.count
      || Object.keys(geometry.attributes).filter(name => name.startsWith('_kit_')).length !== names.length) return null;
    for (const { name, itemSize, offset } of fields) {
      const attribute = geometry.getAttribute(name);
      if (!(attribute instanceof THREE.InterleavedBufferAttribute) || attribute.data !== buffer
        || attribute.itemSize !== itemSize || attribute.offset !== offset || attribute.normalized) return null;
    }
    // Transforming a base attribute must never change bytes belonging to the program.
    for (const [name, attribute] of Object.entries(geometry.attributes)) {
      if (!name.startsWith('_kit_') && attribute instanceof THREE.InterleavedBufferAttribute && attribute.data === buffer) return null;
    }
    buffers.push(buffer);
  }
  return { fields, stride, buffers };
}

/** Clone the same base attributes and bounds without copying immutable facade program data. */
function cloneBase(geometry: THREE.BufferGeometry) {
  const base = new THREE.BufferGeometry();
  base.index = geometry.index;
  base.attributes = Object.fromEntries(Object.entries(geometry.attributes).filter(([name]) => !name.startsWith('_kit_')));
  base.boundingBox = geometry.boundingBox; base.boundingSphere = geometry.boundingSphere;
  base.morphTargetsRelative = geometry.morphTargetsRelative;
  return base.clone();
}

/** Merge opaque static neighbours with the same material/layout, retaining semantic index ranges.
 * Small spatial buckets preserve culling; mutable props, instances and transparent ordering stay separate. */
export function* staticBatchSteps(model: THREE.Object3D): Generator<void> {
  if (owners.has(model)) return;
  model.updateWorldMatrix(true, true);
  const buckets = new Map<string, THREE.Mesh[]>(), center = new THREE.Vector3();
  const geometryUsers = new Map<THREE.BufferGeometry, number>();
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (mesh.isMesh) geometryUsers.set(mesh.geometry, (geometryUsers.get(mesh.geometry) ?? 0) + 1);
    if (!mesh.isMesh || !mesh.visible || mesh.children.length || !mesh.parent || Array.isArray(mesh.material)
      || (mesh as THREE.InstancedMesh).isInstancedMesh || (mesh as THREE.SkinnedMesh).isSkinnedMesh
      || mesh.material.transparent || mesh.geometry.groups.length || Object.keys(mesh.geometry.morphAttributes).length
      || mesh.geometry.drawRange.start !== 0 || mesh.geometry.drawRange.count !== Infinity
      || !['ground', 'buildings', 'structures'].includes(mesh.userData.layer)) return;
    // Explicitly dynamic structures (breakable poles etc.) must retain their object/range owners.
    if (mesh.userData.prop_type || mesh.userData.objects?.some((row: Record<string, unknown>) => row.prop_type)) return;
    mesh.geometry.computeBoundingBox();
    mesh.geometry.boundingBox!.getCenter(center).applyMatrix4(mesh.matrixWorld);
    const layout = Object.entries(mesh.geometry.attributes).map(([name, a]) => `${name}:${a.itemSize}:${a.normalized}`).sort();
    const key = JSON.stringify([mesh.parent.uuid, mesh.material.uuid, mesh.userData.layer, mesh.layers.mask,
      mesh.renderOrder, Math.floor(center.x / 62.5), Math.floor(center.z / 62.5), !!mesh.geometry.index, layout]);
    const bucket = buckets.get(key) ?? []; bucket.push(mesh); buckets.set(key, bucket);
  });
  const batches: Batch[] = []; owners.set(model, batches);
  for (const sources of buckets.values()) {
    if (sources.length < 2) continue;
    yield;
    const parent = sources[0].parent!, inverse = parent.matrixWorld.clone().invert();
    const program = packedPrograms(sources);
    const packed = program ? new Float32Array(program.buffers.reduce((sum, buffer) => sum + buffer.array.length, 0)) : null;
    const geometries: THREE.BufferGeometry[] = [], rows: Record<string, unknown>[] = [];
    let offset = 0, programOffset = 0;
    for (const [sourceIndex, source] of sources.entries()) {
      yield;
      const geometry = (program ? cloneBase(source.geometry) : source.geometry.clone())
        .applyMatrix4(inverse.clone().multiply(source.matrixWorld));
      if (program && packed) {
        const array = program.buffers[sourceIndex].array;
        packed.set(array, programOffset); programOffset += array.length;
      }
      geometries.push(geometry);
      const count = geometry.index?.count ?? geometry.attributes.position.count;
      const objects = source.userData.objects ?? [{ ...source.userData, start: 0, count }];
      for (const row of objects) rows.push({ ...row, start: row.start + offset });
      offset += count;
    }
    const geometry = mergeGeometries(geometries);
    for (const temporary of geometries) temporary.dispose();
    if (!geometry) continue;
    if (program && packed) {
      const buffer = new THREE.InterleavedBuffer(packed, program.stride);
      for (const { name, itemSize, offset } of program.fields)
        geometry.setAttribute(name, new THREE.InterleavedBufferAttribute(buffer, itemSize, offset));
    } else interleaveProgram(geometry);
    geometry.computeBoundingBox(); geometry.computeBoundingSphere();
    const mesh = new THREE.Mesh(geometry, sources[0].material);
    mesh.name = 'static-tile-batch'; mesh.userData = { layer: sources[0].userData.layer, objects: rows };
    mesh.layers.mask = sources[0].layers.mask; mesh.renderOrder = sources[0].renderOrder;
    mesh.castShadow = mesh.receiveShadow = true; mesh.matrixAutoUpdate = false;
    if (program && packed) {
      // Detached sources return on release. Their immutable program values can share the
      // merged storage, but distinct attribute owners preserve draw/disposal lifetimes.
      let start = 0;
      for (const [index, source] of sources.entries()) {
        const original = program.buffers[index], end = start + original.array.length;
        if (geometryUsers.get(source.geometry) === 1 && original.usage === THREE.StaticDrawUsage
          && !original.updateRanges.length && original.onUploadCallback === THREE.InterleavedBuffer.prototype.onUploadCallback) {
          const buffer = new THREE.InterleavedBuffer(packed.subarray(start, end), program.stride);
          for (const { name, itemSize, offset } of program.fields) {
            const attribute = new THREE.InterleavedBufferAttribute(buffer, itemSize, offset);
            attribute.name = source.geometry.getAttribute(name).name;
            source.geometry.setAttribute(name, attribute);
          }
        }
        start = end;
      }
    }
    for (const source of sources) source.removeFromParent();
    parent.add(mesh); batches.push({ mesh, sources, parent });
  }
}
export async function prepareStaticBatches(model: THREE.Object3D) {
  for (const _step of staticBatchSteps(model)) { void _step; const pending = budget.checkpoint(); if (pending) await pending; }
}
export function releaseStaticBatches(model: THREE.Object3D, retire: (release: () => void) => void = release => release()) {
  for (const { mesh, sources, parent } of owners.get(model) ?? []) {
    mesh.removeFromParent(); parent.add(...sources);
    retire(() => { disposeDraw(mesh); mesh.geometry.dispose(); });
  }
  owners.delete(model);
}
