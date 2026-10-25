import * as THREE from 'three/webgpu';

// The tile materials must stay separate for facades and object IDs. Shadows only
// need their geometry, so one mesh can cast the same silhouette for the tile.
export function batchTileShadows(model: THREE.Object3D): THREE.Mesh | null {
  model.updateWorldMatrix(true, true);
  const toModel = model.matrixWorld.clone().invert();
  const point = new THREE.Vector3();
  const instance = new THREE.Matrix4();
  const transform = new THREE.Matrix4();
  const positions: number[] = [];
  const indices: number[] = [];
  model.traverse(object => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.frustumCulled = true;
    let owner: THREE.Object3D | null = mesh;
    while (owner && !owner.userData.layer) owner = owner.parent;
    if (owner?.userData.layer === 'ground') {
      mesh.castShadow = false;
      return;
    }
    const geometry = mesh.geometry;
    const attribute = geometry.getAttribute('position');
    if (!attribute) return;
    const index = geometry.getIndex();
    const count = (mesh as THREE.InstancedMesh).isInstancedMesh ? (mesh as THREE.InstancedMesh).count : 1;
    const base = toModel.clone().multiply(mesh.matrixWorld);
    for (let n = 0; n < count; n++) {
      transform.copy(base);
      if ((mesh as THREE.InstancedMesh).isInstancedMesh) {
        (mesh as THREE.InstancedMesh).getMatrixAt(n, instance);
        transform.multiply(instance);
      }
      const offset = positions.length / 3;
      for (let i = 0; i < attribute.count; i++) {
        point.fromBufferAttribute(attribute, i).applyMatrix4(transform);
        positions.push(point.x, point.y, point.z);
      }
      if (index) for (let i = 0; i < index.count; i++) indices.push(offset + index.getX(i));
      else for (let i = 0; i < attribute.count; i++) indices.push(offset + i);
    }
    mesh.castShadow = false;
  });
  if (!indices.length) return null;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(indices);
  geometry.computeBoundingSphere();
  const material = new THREE.MeshBasicMaterial({ colorWrite: false, depthWrite: false });
  const proxy = new THREE.Mesh(geometry, material);
  proxy.name = 'tile-shadow-batch';
  proxy.castShadow = true;
  proxy.frustumCulled = true;
  proxy.userData.shadowBatch = true;
  model.add(proxy);
  return proxy;
}
