import * as THREE from 'three/webgpu';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
// @ts-expect-error three ships no declaration for its meshopt simplifier module (world/collisionPreviewArrays.ts Simplifier).
import { MeshoptSimplifier } from 'three/addons/libs/meshopt_simplifier.module.js';
import { collisionPreviewArrays, type PreviewArrays } from './collisionPreviewArrays';

// A texture-free presentation of the exact cached collision surfaces. Full generated
// facades replace it atomically; ground never waits for their textures or shader programs.
// The worker decodes, merges and bounds the cells; untextured previews shade flat, so they carry no normals.
let worker: Worker | undefined, sequence = 0;
const pending = new Map<number, { resolve: (arrays: PreviewArrays) => void; reject: (error: Error) => void }>();

function build(buffers: ArrayBuffer[]): Promise<PreviewArrays> {
  // Node-side fixtures have no browser Worker.
  if (typeof Worker === 'undefined') return Promise.all([MeshoptDecoder.ready, MeshoptSimplifier.ready]).then(() => collisionPreviewArrays(buffers, MeshoptDecoder, MeshoptSimplifier));
  if (!worker) {
    worker = new Worker(new URL('./collisionPreview.worker.ts', import.meta.url), { type: 'module', name: 'collision-preview' });
    worker.onmessage = ({ data }) => {
      const job = pending.get(data.id); pending.delete(data.id);
      if (data.error) job?.reject(new Error(data.error)); else job?.resolve(data.arrays);
    };
    worker.onerror = event => {
      for (const job of pending.values()) job.reject(new Error(event.message));
      pending.clear(); worker?.terminate(); worker = undefined;
    };
  }
  const id = ++sequence;
  // The cells stay retained for the collision streamer: the worker gets copies.
  const copies = buffers.map(buffer => buffer.slice(0));
  return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); worker!.postMessage({ id, buffers: copies }, copies); });
}

export async function collisionPreview(buffers: ReadonlyMap<string, ArrayBuffer>): Promise<THREE.Object3D> {
  const arrays = await build([...buffers.values()]);
  if (!arrays.index.length) throw new Error('Cannot combine cached collision surfaces');
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(arrays.position, 3));
  geometry.setAttribute('color', new THREE.BufferAttribute(arrays.color, 3));
  geometry.setIndex(new THREE.BufferAttribute(arrays.index, 1));
  geometry.boundingBox = new THREE.Box3(new THREE.Vector3(...arrays.min), new THREE.Vector3(...arrays.max));
  geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(arrays.sphere[0], arrays.sphere[1], arrays.sphere[2]), arrays.sphere[3]);
  const model = new THREE.Group();
  model.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 1, side: THREE.DoubleSide, flatShading: true })));
  model.userData.collisionPreview = true;
  return model;
}
