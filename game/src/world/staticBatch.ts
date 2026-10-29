import * as THREE from 'three/webgpu';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { interleaveProgram } from '../render/facadeProgramAttributes';
import { disposeDraw } from '../render/sharedMaterials';
import { PreparationBudget } from '../render/preparationBudget';

interface Batch { mesh: THREE.Mesh; sources: THREE.Mesh[]; parent: THREE.Object3D }
const owners = new WeakMap<THREE.Object3D, Batch[]>();
const budget = new PreparationBudget();

/** Merge opaque static neighbours with the same material/layout, retaining semantic index ranges.
 * Small spatial buckets preserve culling; mutable props, instances and transparent ordering stay separate. */
export function* staticBatchSteps(model: THREE.Object3D): Generator<void> {
  if (owners.has(model)) return;
  model.updateWorldMatrix(true, true);
  const buckets = new Map<string, THREE.Mesh[]>(), center = new THREE.Vector3();
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
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
    const geometries: THREE.BufferGeometry[] = [], rows: Record<string, unknown>[] = [];
    let offset = 0;
    for (const source of sources) {
      yield;
      const geometry = source.geometry.clone().applyMatrix4(inverse.clone().multiply(source.matrixWorld));
      geometries.push(geometry);
      const count = geometry.index?.count ?? geometry.attributes.position.count;
      const objects = source.userData.objects ?? [{ ...source.userData, start: 0, count }];
      for (const row of objects) rows.push({ ...row, start: row.start + offset });
      offset += count;
    }
    const geometry = mergeGeometries(geometries);
    for (const temporary of geometries) temporary.dispose();
    if (!geometry) continue;
    interleaveProgram(geometry);
    geometry.computeBoundingBox(); geometry.computeBoundingSphere();
    const mesh = new THREE.Mesh(geometry, sources[0].material);
    mesh.name = 'static-tile-batch'; mesh.userData = { layer: sources[0].userData.layer, objects: rows };
    mesh.layers.mask = sources[0].layers.mask; mesh.renderOrder = sources[0].renderOrder;
    mesh.castShadow = mesh.receiveShadow = true; mesh.matrixAutoUpdate = false;
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
