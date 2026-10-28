import * as THREE from 'three/webgpu';
import { vegetationOwner } from './vegetationLod';
import { shadowChunks } from './shadowChunks';

// Colour, normal and reflection cameras use layer 0; sun shadow cameras also enable this layer.
export const TILE_SHADOW_LAYER = 1;

// The tile materials must stay separate for facades and object IDs. Shadows only
// need their geometry. Spatial chunks retain the silhouette without drawing an entire 250 m tile per cascade.
export function batchTileShadows(model: THREE.Object3D): THREE.Mesh | null {
  model.updateWorldMatrix(true, true);
  const toModel = model.matrixWorld.clone().invert();
  const point = new THREE.Vector3();
  const instance = new THREE.Matrix4();
  const transform = new THREE.Matrix4();
  const meshes: THREE.Mesh[] = [];
  let vertexCount = 0, indexCount = 0;
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.frustumCulled = true;
    if (mesh.userData.prop_type === 'sign' || mesh.userData.prop_type === 'bus_stop'
      || (Array.isArray(mesh.userData.objects) && mesh.userData.objects.some((e: Record<string, unknown>) =>
        e.prop_type === 'sign' || e.prop_type === 'bus_stop'))) {
      // These ranges are removed as posts break. Keep their shadow tied to the live mesh.
      return;
    }
    let owner: THREE.Object3D | null = mesh;
    while (owner && !owner.userData.layer) owner = owner.parent;
    if (owner?.userData.layer === 'ground') {
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
      for (let i = 0; i < attribute.count; i++) {
        point.fromBufferAttribute(attribute, i).applyMatrix4(transform);
        positions[vertexOffset * 3] = point.x; positions[vertexOffset * 3 + 1] = point.y; positions[vertexOffset * 3 + 2] = point.z;
        vertexOffset++;
      }
      if (index) for (let i = 0; i < index.count; i++) indices[indexOffset++] = offset + index.getX(i);
      else for (let i = 0; i < attribute.count; i++) indices[indexOffset++] = offset + i;
    }
  }
  const material = new THREE.MeshBasicMaterial({ colorWrite: false, depthWrite: false });
  const chunks = shadowChunks(positions, indices, model.matrixWorld).map(geometry => {
    const proxy = new THREE.Mesh(geometry, material);
    proxy.name = 'tile-shadow-batch';
    proxy.castShadow = true;
    proxy.layers.set(TILE_SHADOW_LAYER);
    proxy.frustumCulled = true;
    proxy.userData.shadowBatch = true;
    return proxy;
  });
  // Children are independently frustum tested even when their parent's mesh is outside a cascade.
  const proxy = chunks[0];
  proxy.add(...chunks.slice(1));
  model.add(proxy);
  return proxy;
}
