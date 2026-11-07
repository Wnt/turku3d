import * as THREE from 'three/webgpu';
import { vegetationOwner } from './vegetationLod';
import { drain, shadowChunkSteps } from './shadowChunks';
import { streamingBudget } from '../render/preparationBudget';

// Colour, normal and reflection cameras use layer 0; sun shadow cameras also enable this layer.
export const TILE_SHADOW_LAYER = 1;
/** A tile shadow batch's 62.5 m chunks, each culled on its own: only the nearest cascade sees them. */
export const TILE_SHADOW_CHUNK_LAYER = 4;
/** A tile shadow batch drawn whole, one draw per tile: the outer cascades see it instead of the chunks
 *  (render/environment.ts cascadeLayers). Both share one vertex and one index buffer. */
export const TILE_SHADOW_WHOLE_LAYER = 5;

// The tile materials must stay separate for facades and object IDs. Shadows only
// need their geometry. Spatial chunks retain the silhouette without drawing an entire 250 m tile per cascade.
export function batchTileShadows(model: THREE.Object3D): THREE.Mesh | null {
  return drain(shadowBatchSteps(model));
}

const budget = streamingBudget();
export async function prepareTileShadows(model: THREE.Object3D): Promise<THREE.Mesh | null> {
  const steps = shadowBatchSteps(model);
  let next = steps.next();
  while (!next.done) {
    const pending = budget.checkpoint();
    if (pending) await pending;
    next = steps.next();
  }
  return next.value;
}

function* shadowBatchSteps(model: THREE.Object3D): Generator<void, THREE.Mesh | null> {
  model.updateWorldMatrix(true, true);
  const toModel = model.matrixWorld.clone().invert();
  const point = new THREE.Vector3();
  const instance = new THREE.Matrix4();
  const transform = new THREE.Matrix4();
  const meshes: THREE.Mesh[] = [];
  let vertexCount = 0, indexCount = 0;
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh || mesh.userData.facadeModules) return;
    mesh.frustumCulled = true;
    if (mesh.userData.prop_type === 'sign' || mesh.userData.prop_type === 'bus_stop'
      || (Array.isArray(mesh.userData.objects) && mesh.userData.objects.some((e: Record<string, unknown>) =>
        e.prop_type === 'sign' || e.prop_type === 'bus_stop'))) {
      // These ranges are removed as posts break. Keep their shadow tied to the live mesh.
      return;
    }
    let owner: THREE.Object3D | null = mesh;
    while (owner && !owner.userData.layer) owner = owner.parent;
    // Ground and road paint lying on it receive shadows; they never cast any.
    if (owner?.userData.layer === 'ground' || owner?.userData.layer === 'markings') {
      mesh.castShadow = false;
      return;
    }
    if (vegetationOwner(mesh)) {
      mesh.castShadow = false;
      return;
    }
    const geometry = mesh.geometry;
    const attribute = geometry.getAttribute('position');
    if (!attribute) return;
    const count = (mesh as THREE.InstancedMesh).isInstancedMesh ? (mesh as THREE.InstancedMesh).count : 1;
    vertexCount += attribute.count * count;
    indexCount += (geometry.index?.count ?? attribute.count) * count;
    meshes.push(mesh);
    mesh.castShadow = false;
  });
  if (!indexCount) return null;
  // Allocate the final buffers once. Growing JS number arrays and copying them doubles a large tile's heap burst.
  const positions = new Float32Array(vertexCount * 3);
  const indices = vertexCount > 65535 ? new Uint32Array(indexCount) : new Uint16Array(indexCount);
  let vertexOffset = 0, indexOffset = 0;
  for (const mesh of meshes) {
    const attribute = mesh.geometry.getAttribute('position'), index = mesh.geometry.index;
    const count = (mesh as THREE.InstancedMesh).isInstancedMesh ? (mesh as THREE.InstancedMesh).count : 1;
    const base = toModel.clone().multiply(mesh.matrixWorld);
    for (let n = 0; n < count; n++) {
      transform.copy(base);
      if ((mesh as THREE.InstancedMesh).isInstancedMesh) {
        (mesh as THREE.InstancedMesh).getMatrixAt(n, instance);
        transform.multiply(instance);
      }
      const offset = vertexOffset;
      // Plain (unnormalized, not interleaved) positions are read from their array with applyMatrix4's arithmetic:
      // Vector3.fromBufferAttribute/applyMatrix4 per vertex and index getX were 1.2 s of a 6.7-min Windows drive.
      const plain = (attribute as THREE.BufferAttribute).isBufferAttribute && !attribute.normalized ? attribute.array : null;
      const e = transform.elements, stride = attribute.itemSize;
      for (let i = 0; i < attribute.count; i++) {
        if (i % 4096 === 0) yield;
        if (plain) {
          const x = plain[i * stride], y = plain[i * stride + 1], z = plain[i * stride + 2];
          const w = 1 / (e[3] * x + e[7] * y + e[11] * z + e[15]);
          positions[vertexOffset * 3] = (e[0] * x + e[4] * y + e[8] * z + e[12]) * w;
          positions[vertexOffset * 3 + 1] = (e[1] * x + e[5] * y + e[9] * z + e[13]) * w;
          positions[vertexOffset * 3 + 2] = (e[2] * x + e[6] * y + e[10] * z + e[14]) * w;
        } else {
          point.fromBufferAttribute(attribute, i).applyMatrix4(transform);
          positions[vertexOffset * 3] = point.x; positions[vertexOffset * 3 + 1] = point.y; positions[vertexOffset * 3 + 2] = point.z;
        }
        vertexOffset++;
      }
      if (index) {
        const source = index.array;
        for (let i = 0; i < index.count; i++) {
          if (i % 4096 === 0) yield;
          indices[indexOffset++] = offset + source[i];
        }
      }
      else for (let i = 0; i < attribute.count; i++) indices[indexOffset++] = offset + i;
    }
  }
  const material = new THREE.MeshBasicMaterial({ colorWrite: false, depthWrite: false });
  const { whole, chunks } = yield* shadowChunkSteps(positions, indices, model.matrixWorld);
  const caster = (geometry: THREE.BufferGeometry, layer: number) => {
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = 'tile-shadow-batch';
    mesh.castShadow = true;
    mesh.layers.set(layer);
    mesh.frustumCulled = true;
    mesh.userData.shadowBatch = true;
    return mesh;
  };
  // The whole-tile caster is the root; the chunks are its children, frustum tested on their own (the nearest
  // cascade), while the outer cascades draw the root once per tile. Shadow maps are unchanged: the same triangles.
  const proxy = caster(whole, TILE_SHADOW_WHOLE_LAYER);
  proxy.add(...chunks.map(geometry => caster(geometry, TILE_SHADOW_CHUNK_LAYER)));
  proxy.userData.shadowTriangles = indexCount / 3;
  model.add(proxy);
  return proxy;
}
